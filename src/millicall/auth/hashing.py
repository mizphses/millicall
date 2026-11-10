"""Argon2 の高コスト計算をイベントループ外で実行する集約ヘルパ（issue #81）。

背景:
  Argon2 の hash/verify は 1 回あたり数十 ms の CPU とメモリ（既定 64 MiB）を使う。
  async ハンドラ内で同期実行すると、その間は同一イベントループ上の音声 WS・ESL イベント
  処理が止まる。リカバリコード検証は最大 10 回連続で回るため停止は数百 ms に達する。

設計:
  * 用途別の「レーン」ごとに専用の小さな ThreadPoolExecutor で実行する。
    argon2-cffi は計算中に GIL を解放するため、ループは計算と並行して進む。
      - ``auth`` レーン（2 本）: /login・TOTP・ユーザー管理・SAML・SCIM トークン発行
        （Cookie 認証の管理操作）。
      - ``scim`` レーン（1 本）: SCIM Bearer クライアント起点の計算（Bearer 検証・
        SCIM ユーザー作成）。SCIM の Bearer 失敗にはレート制限が無いため、不正トークンの
        大量送信が /login の計算待ちを伸ばさないよう別レーンに隔離する。/login は
        レート制限で SQLite 書き込みロックを取ったまま計算を待つため、ここでの待ちは
        そのまま他の書き込みの停止時間になる。
  * スレッド数の上限 = そのレーンの同時計算数の上限（スレッドは増えない）。
  * レーンごとに「投入済み・未完了」ジョブ数の上限を設け、超過時は計算せずに
    503 + Retry-After（:class:`Argon2Busy`）を返す。待ち行列を無制限に伸ばすと、
    待ち時間が SQLite の busy タイムアウト（sqlite3 既定 5 秒）や HTTP タイムアウトを
    超えて結局失敗するうえ、その間ロックとメモリを握り続けるため早期に断る。
  * 待機中に呼び出し元がキャンセルされた場合、未開始のジョブは concurrent future ごと
    取り消すため計算されない（``asyncio.wrap_future`` がキャンセルを伝播する）。
  * 既定 executor（``asyncio.to_thread``）と分けるのは、認証の総当たり負荷が DNS 解決や
    TTS 合成等の他のオフロード処理を飢餓させないため。
  * 上限は設定値ではなく定数にする。1 計算あたり 64 MiB を確保するため、上限を上げると
    認証負荷だけでメモリ・CPU を食い潰し音声処理を圧迫する。レート制限（auth/throttle.py）
    は計算前に効くため、正規の利用で上限を超える同時計算が必要になる場面はない。
  * レート制限・監査記録・タイミングガード（ダミーハッシュ）の順序は呼び出し元が従来どおり
    維持する。本モジュールは「どこで計算するか」だけを変える。Argon2Busy はユーザーの
    存否と無関係に（計算前に）発生するため、ユーザー列挙のオラクルにはならない。

リカバリコード:
  生成形式（``xxxxx-xxxxx-xxxxx-xxxxx``、小文字 hex）に合致しない入力は、どの保存
  ハッシュとも一致し得ないため Argon2 を回さずに不一致とする。呼び出し元はこれを通常の
  不一致と同じ経路（同じ失敗記録・監査・レスポンス）で扱うこと。形式は公開情報のため、
  形式不正の拒否が速いことで秘密は漏れない。
"""

import asyncio
import re
import secrets
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import HTTPException, status

from millicall.auth.security import hash_password, verify_password

# auth レーンの同時計算数の上限（理由はモジュール docstring 参照）。
_ARGON2_MAX_WORKERS = 2
# auth レーンの投入済み・未完了ジョブ数の上限。1 計算 ≈ 40ms・2 本並列で 64 件 ≈ 1.3 秒。
# 最悪待ちを SQLite の busy タイムアウト（5 秒）より十分短く保つ値にしている。
# リカバリコード検証（最大 10 件を 1 件ずつ投入）が複数同時に来ても収まる余裕も持たせる。
_AUTH_MAX_PENDING = 64
# scim レーン。IdP は通常逐次にリクエストするため 1 本で足りる。
# 32 件 ≈ 1 本で 1.3 秒。不正トークンの大量送信はこれを超えた時点で 503 になる。
_SCIM_MAX_WORKERS = 1
_SCIM_MAX_PENDING = 32
# 503 の Retry-After（秒）。待ち行列はおおむね 1〜2 秒で捌けるため短めにする。
_BUSY_RETRY_AFTER_SECONDS = 2

AUTH_LANE = "auth"
SCIM_LANE = "scim"


class Argon2Busy(HTTPException):
    """レーンの待ち行列が上限に達した。FastAPI がそのまま 503 + Retry-After に変換する。"""

    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service busy",
            headers={"Retry-After": str(_BUSY_RETRY_AFTER_SECONDS)},
        )


class _Lane:
    """専用 executor + 未完了ジョブ数の上限を持つ計算レーン。"""

    def __init__(self, name: str, max_workers: int, max_pending: int) -> None:
        self.name = name
        self.max_workers = max_workers
        self.max_pending = max_pending
        self._lock = threading.Lock()
        self._pending = 0
        self._executor: ThreadPoolExecutor | None = None

    @property
    def pending(self) -> int:
        return self._pending

    def _on_done(self, _fut: Future) -> None:
        # ワーカースレッド（またはキャンセル時は呼び出しスレッド）から呼ばれる
        with self._lock:
            self._pending -= 1

    def submit(self, fn: Callable, *args: object) -> Future:
        with self._lock:
            if self._pending >= self.max_pending:
                raise Argon2Busy()
            if self._executor is None:
                # shutdown 後（テストでの lifespan 再起動等）は次回投入時に作り直す
                self._executor = ThreadPoolExecutor(
                    max_workers=self.max_workers, thread_name_prefix=f"argon2-{self.name}"
                )
            fut = self._executor.submit(fn, *args)
            self._pending += 1
        fut.add_done_callback(self._on_done)
        return fut

    def shutdown(self) -> None:
        with self._lock:
            executor, self._executor = self._executor, None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)


_LANES = {
    AUTH_LANE: _Lane(AUTH_LANE, _ARGON2_MAX_WORKERS, _AUTH_MAX_PENDING),
    SCIM_LANE: _Lane(SCIM_LANE, _SCIM_MAX_WORKERS, _SCIM_MAX_PENDING),
}


def shutdown() -> None:
    """全レーンの executor を停止する（lifespan 終了時）。未開始ジョブは取り消す。"""
    for lane in _LANES.values():
        lane.shutdown()


_hasher = PasswordHasher()

# リカバリコードの生成数
RECOVERY_CODE_COUNT = 10

# 生成形式: 20 文字の小文字 hex を 5 文字ずつハイフンで区切ったもの
_RECOVERY_CODE_RE = re.compile(r"[0-9a-f]{5}(?:-[0-9a-f]{5}){3}")


async def run_argon2[T](fn: Callable[..., T], *args: object, lane: str = AUTH_LANE) -> T:
    """Argon2 を使う同期関数をレーンの専用 executor で実行する。

    Raises:
        Argon2Busy: レーンの未完了ジョブ数が上限に達している（計算はしない）。
    """
    return await asyncio.wrap_future(_LANES[lane].submit(fn, *args))


async def hash_password_async(password: str, *, lane: str = AUTH_LANE) -> str:
    return await run_argon2(hash_password, password, lane=lane)


async def verify_password_async(hashed: str, password: str, *, lane: str = AUTH_LANE) -> bool:
    return await run_argon2(verify_password, hashed, password, lane=lane)


def _verify_or_false(hashed: str, secret: str) -> bool:
    """Argon2 verify。不一致・ハッシュ破損などの例外はすべて False に畳む。"""
    try:
        return _hasher.verify(hashed, secret)
    except VerifyMismatchError:
        return False
    except Exception:  # noqa: BLE001
        return False


def is_recovery_code_format(code: str) -> bool:
    """生成形式のリカバリコードかどうか（Argon2 を回す価値があるか）を返す。"""
    return _RECOVERY_CODE_RE.fullmatch(code) is not None


async def find_recovery_code(stored_hashes: list[str], code: str) -> int | None:
    """リカバリコードを保存ハッシュと比較し、一致したインデックスを返す。

    形式不正の入力は Argon2 を回さずに None（不一致と同じ）を返す。
    1 ハッシュずつ executor に投入するため、10 件の検証中も他の Argon2 要求が割り込める。
    """
    if not is_recovery_code_format(code):
        return None
    for i, h in enumerate(stored_hashes):
        if await run_argon2(_verify_or_false, h, code):
            return i
    return None


def find_recovery_code_sync(stored_hashes: list[str], code: str) -> int | None:
    """:func:`find_recovery_code` の同期版（ループ上で計算する。async 経路では使わない）。

    同期呼び出し元（``auth.router._verify_code`` 経由の mcp_server/login.py）向けに残す。
    形式不正で Argon2 を回さない挙動は共通。
    """
    if not is_recovery_code_format(code):
        return None
    for i, h in enumerate(stored_hashes):
        if _verify_or_false(h, code):
            return i
    return None


async def generate_recovery_codes() -> tuple[list[str], list[str]]:
    """リカバリコードを ``RECOVERY_CODE_COUNT`` 個生成する。

    Returns:
        (plaintext_codes, hashed_codes) — hashed_codes のみ DB に保存する
    """
    plain: list[str] = []
    hashed: list[str] = []
    for _ in range(RECOVERY_CODE_COUNT):
        raw = secrets.token_hex(10)  # 20 文字の hex 文字列
        # 人間が読みやすい形式: xxxxx-xxxxx-xxxxx-xxxxx（4 × 5 文字）
        code = f"{raw[:5]}-{raw[5:10]}-{raw[10:15]}-{raw[15:20]}"
        plain.append(code)
        hashed.append(await run_argon2(_hasher.hash, code))
    return plain, hashed
