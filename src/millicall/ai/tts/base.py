from typing import Protocol, runtime_checkable


@runtime_checkable
class TTSProvider(Protocol):
    async def synthesize(self, text: str) -> bytes:
        """text を合成し L16 モノ 8000Hz のヘッダレス PCM を返す。"""
        ...
