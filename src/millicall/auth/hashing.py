"""Argon2 の高コスト計算をイベントループ外で実行する集約ヘルパ（issue #81）。

背景:
  Argon2 の hash/verify は 1 回あたり数十 ms の CPU とメモリ（既定 64 MiB）を使う。
  async ハンドラ内で同期実行すると、その間は同一イベントループ上の音声 WS・ESL イベント
  処理が止まる。リカバリコード検証は最大 10 回連続で回るため停止は数百 ms に達する。

設計:
  * 専用の小さな ThreadPoolExecutor（``_ARGON2_MAX_WORKERS`` 本）で実行する。
    argon2-cffi は計算中に GIL を解放するため、ループは計算と並行して進む。
  * スレッド数の上限 = Argon2 の同時計算数の上限。上限を超えた要求は executor の
    キューで待つ（スレッドは増えない）。待機中に呼び出し元がキャンセルされた場合、
    未開始のジョブは ``run_in_executor`` が concurrent future ごと取り消すため計算されない。
  * 既定 executor（``asyncio.to_thread``）と分けるのは、認証の総当たり負荷が DNS 解決や
    TTS 合成等の他のオフロード処理を飢餓させないため。
  * 上限は設定値ではなく定数にする。1 計算あたり 64 MiB を確保するため、上限を上げると
    認証負荷だけでメモリ・CPU を食い潰し音声処理を圧迫する。レート制限（auth/throttle.py）
    は計算前に効くため、正規の利用で 2 本を超える同時計算が必要になる場面はない。
  * レート制限・監査記録・タイミングガード（ダミーハッシュ）の順序は呼び出し元が従来どおり
    維持する。本モジュールは「どこで計算するか」だけを変える。

リカバリコード:
  生成形式（``xxxxx-xxxxx-xxxxx-xxxxx``、小文字 hex）に合致しない入力は、どの保存
  ハッシュとも一致し得ないため Argon2 を回さずに不一致とする。呼び出し元はこれを通常の
  不一致と同じ経路（同じ失敗記録・監査・レスポンス）で扱うこと。形式は公開情報のため、
  形式不正の拒否が速いことで秘密は漏れない。
"""

import asyncio
import re
import secrets
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from millicall.auth.security import hash_password, verify_password

# Argon2 の同時計算数の上限（理由はモジュール docstring 参照）。
_ARGON2_MAX_WORKERS = 2

_executor = ThreadPoolExecutor(max_workers=_ARGON2_MAX_WORKERS, thread_name_prefix="argon2")

_hasher = PasswordHasher()

# リカバリコードの生成数
RECOVERY_CODE_COUNT = 10

# 生成形式: 20 文字の小文字 hex を 5 文字ずつハイフンで区切ったもの
_RECOVERY_CODE_RE = re.compile(r"[0-9a-f]{5}(?:-[0-9a-f]{5}){3}")


async def run_argon2[T](fn: Callable[..., T], *args: object) -> T:
    """Argon2 を使う同期関数を専用 executor で実行する（同時計算数は上限付き）。"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, fn, *args)


async def hash_password_async(password: str) -> str:
    return await run_argon2(hash_password, password)


async def verify_password_async(hashed: str, password: str) -> bool:
    return await run_argon2(verify_password, hashed, password)


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
