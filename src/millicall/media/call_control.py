"""通話中の parked チャネルを制御する CallControl 抽象と ESL 実装。

再生は `uuid_broadcast`、バージイン停止は `uuid_break`、切断は `uuid_kill` を
ESL(`bgapi`) 経由で送る。再生完了は FreeSWITCH の PLAYBACK_STOP イベントで
非同期に通知される（Task 15 の MediaEventRouter が該当 uuid の
`EslCallControl._notify_playback_done()` を呼ぶ）。
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol

from millicall.telephony.esl import ESLConnectionClosed


class CallControl(Protocol):
    async def play_file(self, path: str) -> None:
        ...

    async def stop_playback(self) -> None:
        ...

    async def hangup(self) -> None:
        ...


# ESL クライアントに要求する最小インターフェース（`bgapi` のみ）。
class _EslLike(Protocol):
    async def bgapi(self, command: str) -> str:
        ...


class EslCallControl:
    """ESL 経由で parked チャネルを制御する CallControl 実装。

    ESL コマンド接続の管理（プランレビュー I6）:
        単一の ESL 接続を複数通話で共有する場合、並行する bgapi の書き込みが
        混線しないよう、呼び出し元が共有 `lock` を注入して直列化する。
        接続断時は注入された `reconnect` コールバックで張り直して再送する。
        いずれも省略時は per-call 専用接続・再接続なしの単純動作にフォールバックする
        （その場合は接続断が呼び出し元へ伝播する）。
    """

    def __init__(
        self,
        esl: _EslLike,
        uuid: str,
        *,
        lock: asyncio.Lock | None = None,
        reconnect: Callable[[], Awaitable[_EslLike]] | None = None,
    ) -> None:
        self._esl = esl
        self._uuid = uuid
        self._playback_done = asyncio.Event()
        self._lock = lock if lock is not None else asyncio.Lock()
        self._reconnect = reconnect

    def _notify_playback_done(self) -> None:
        """PLAYBACK_STOP 受信時にイベントルータから呼ばれる（Task 14/15 固定契約）。"""
        self._playback_done.set()

    async def _bgapi(self, command: str) -> None:
        """共有接続をロックで直列化し、接続断時は reconnect で張り直して再送する。"""
        async with self._lock:
            try:
                await self._esl.bgapi(command)
            except ESLConnectionClosed:
                if self._reconnect is None:
                    raise
                self._esl = await self._reconnect()
                await self._esl.bgapi(command)

    async def play_file(self, path: str) -> None:
        self._playback_done.clear()
        await self._bgapi(f"uuid_broadcast {self._uuid} {path} aleg")
        await self._playback_done.wait()

    async def stop_playback(self) -> None:
        await self._bgapi(f"uuid_break {self._uuid} all")

    async def hangup(self) -> None:
        await self._bgapi(f"uuid_kill {self._uuid}")
