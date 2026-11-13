"""tools 共通: DB セッションの用意と出力。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from millicall.config import Settings
from millicall.db import create_db_engine

RESTART_HINT = (
    "FreeSWITCH 設定へ反映するには core を再起動してください（millicallctl restart）。"
    "起動時に設定が再生成されます。"
)


@asynccontextmanager
async def open_session(settings: Settings) -> AsyncIterator[AsyncSession]:
    engine = create_db_engine(settings.database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield session
    finally:
        await engine.dispose()


def out(msg: str = "") -> None:
    # 平文の認証情報は決して渡さないこと（番号・名前・件数のみ）。
    print(msg, flush=True)
