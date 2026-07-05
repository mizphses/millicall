import pytest
from sqlalchemy import inspect

from millicall.db import create_db_engine
from millicall.db_migrations import upgrade_to_head


@pytest.mark.asyncio
async def test_call_messages_table(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'm.db'}"
    upgrade_to_head(url)
    engine = create_db_engine(url)
    async with engine.connect() as conn:
        cols = await conn.run_sync(
            lambda c: [col["name"] for col in inspect(c).get_columns("call_messages")]
        )
    await engine.dispose()
    assert {"call_uuid", "role", "text", "latency_ms"} <= set(cols)
