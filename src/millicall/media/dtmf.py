"""FreeSWITCH DTMF イベントを per-uuid キューへ振り分けるコレクタ (Phase 4b Task 6).

``DtmfCollector`` は MediaEventRouter が受信した DTMF イベントをバッファリングし、
ワークフローハンドラ（dtmf_input / menu）が ``BoundDtmf.collect()`` で非同期に
取り出せるようにする。AnswerRegistry / HangupRegistry と同じスタイルで設計されて
いるが、Future の代わりに asyncio.Queue を使って複数桁の入力を順序付きで保持する。

設計決定:
  * ``feed(uuid, digit)`` は未登録 uuid でも自動登録してキューに入れる（桁を取りこぼさない）。
    ただし ``unregister`` 後の uuid への feed は自動で新規キューを生成するため、
    ハングアップ後に着信する遅延 DTMF イベントも安全に吸収される。
  * ``asyncio.Queue`` はインスタンス化時点の実行中ループにバインドされるため、
    テストでは ``pytest-asyncio`` の event_loop スコープ内で生成すること。
  * ``collect()`` は単調クロック（``asyncio.get_running_loop().time()``）による
    全体デッドラインを設け、per-digit ごとに残余時間を ``wait_for`` に渡す
    （桁間タイムアウト = 全体タイムアウト残時間として機能する）。
  * ``terminator`` が空文字列 ``""`` のときは終端キーなし（max_digits か
    タイムアウトまで収集継続）。
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger("millicall.media.dtmf")


class BoundDtmf:
    """特定 uuid に紐づいた DTMF 収集ビュー。

    ``DtmfCollector.bind(uuid)`` が返す。ctx.dtmf にセットしてハンドラから使う。
    """

    def __init__(self, collector: DtmfCollector, uuid: str) -> None:
        self._collector = collector
        self._uuid = uuid

    async def collect(self, *, max_digits: int, timeout: float, terminator: str) -> str:
        """DTMF 桁を収集して文字列で返す。

        収集終了条件（いずれか最初に達した方）:
          1. ``max_digits`` 桁が集まった。
          2. ``terminator`` が入力された（terminator 自体は結果に含まれない）。
             ``terminator == ""`` のときはこの条件を無効化する。
          3. 最後の桁を受信してから ``timeout`` 秒が経過した（全体デッドライン）。

        戻り値は収集済み桁の文字列（タイムアウトで何も押されなければ空文字列 ``""``）。
        """
        q = self._collector._get_or_create_queue(self._uuid)
        result: list[str] = []
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout

        while len(result) < max_digits:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                digit: str = await asyncio.wait_for(q.get(), timeout=remaining)
            except TimeoutError:
                break

            # terminator チェック（空文字列ならスキップ）
            if terminator and digit == terminator:
                break  # terminator は結果に含めない

            result.append(digit)

        return "".join(result)

    def pending(self) -> bool:
        """該当 uuid のキューに未消費の桁が 1 つ以上あれば True を返す（消費しない）。

        バージイン（プロンプト再生中の DTMF 検出）判定に使う。桁は消費せず、
        後続の ``collect()`` がそのまま拾えるようにする。
        """
        return self._collector.has_pending(self._uuid)


class DtmfCollector:
    """全通話共有の DTMF バッファレジストリ。

    MediaEventRouter に注入し、DTMF イベント受信時に ``feed()`` を呼ぶ。
    ワークフロー実行前に ``register(uuid)`` でキューを初期化し、
    通話終了後に ``unregister(uuid)`` でキューを解放する。
    """

    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue[str]] = {}

    # --- ライフサイクル ---------------------------------------------------- #

    def register(self, uuid: str) -> None:
        """uuid のキューを初期化する。既存のキューがあれば再作成して空にする。"""
        if uuid not in self._queues:
            self._queues[uuid] = asyncio.Queue()

    def unregister(self, uuid: str) -> None:
        """uuid のキューを破棄する。未登録 uuid でも no-op。"""
        self._queues.pop(uuid, None)

    # --- 内部 -------------------------------------------------------------- #

    def _get_or_create_queue(self, uuid: str) -> asyncio.Queue[str]:
        """uuid のキューを返す。存在しない場合は自動登録して返す。"""
        if uuid not in self._queues:
            logger.debug("dtmf: auto-registering uuid=%s on first access", uuid)
            self._queues[uuid] = asyncio.Queue()
        return self._queues[uuid]

    # --- イベント受信 ------------------------------------------------------- #

    def feed(self, uuid: str, digit: str) -> None:
        """DTMF 桁をキューに入れる。

        未登録 uuid への feed は自動登録して受け入れる（ハングアップ後の
        遅延イベント等で桁を取りこぼさないための安全策）。
        空文字列 / None の digit は無視する。
        """
        if not digit:
            return
        q = self._get_or_create_queue(uuid)
        q.put_nowait(digit)

    def has_pending(self, uuid: str) -> bool:
        """uuid のキューに未消費の桁があれば True（消費しない）。

        未登録 uuid は False を返す（自動登録しない — 純粋な参照系）。
        ``BoundDtmf.pending()`` から呼ばれる。
        """
        q = self._queues.get(uuid)
        return q is not None and not q.empty()

    # --- バインド ---------------------------------------------------------- #

    def bind(self, uuid: str) -> BoundDtmf:
        """uuid に紐づいた BoundDtmf オブジェクトを返す。

        ctx.dtmf に代入してハンドラから使う。
        このメソッドを呼んでもキューの自動登録は行わない（register() を別途呼ぶか、
        collect() の最初の q.get() で自動登録が起きる）。
        """
        return BoundDtmf(self, uuid)
