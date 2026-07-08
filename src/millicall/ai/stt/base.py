from collections.abc import Awaitable, Callable
from typing import Protocol, runtime_checkable


@runtime_checkable
class STTSession(Protocol):
    async def feed(self, pcm: bytes) -> None:
        """発話中の PCM チャンクを与える（バッチ系は蓄積のみ）。"""
        ...

    async def finish(self) -> str:
        """発話終端。最終確定テキストを返す。"""
        ...


@runtime_checkable
class STTProvider(Protocol):
    def open_session(self) -> STTSession: ...


class BatchSTTSession:
    """PCM を蓄積し、finish で全体を一括 transcribe する共通セッション。"""

    def __init__(self, transcribe: Callable[[bytes], Awaitable[str]]) -> None:
        self._transcribe = transcribe
        self._buf = bytearray()

    async def feed(self, pcm: bytes) -> None:
        self._buf.extend(pcm)

    async def finish(self) -> str:
        if not self._buf:
            return ""
        return await self._transcribe(bytes(self._buf))
