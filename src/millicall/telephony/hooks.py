from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession


class ExtensionChangeListener(Protocol):
    async def notify(self, session: AsyncSession) -> None: ...


class NullChangeListener:
    """Phase 1 の初期実装。Task 8 で TelephonyChangeListener に差し替える。"""

    async def notify(self, session: AsyncSession) -> None:
        return None
