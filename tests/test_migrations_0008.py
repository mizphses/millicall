import pytest
from sqlalchemy import inspect

from millicall.db import create_db_engine
from millicall.db_migrations import upgrade_to_head


@pytest.mark.asyncio
async def test_providers_table_created(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'm.db'}"
    upgrade_to_head(url)
    engine = create_db_engine(url)
    async with engine.connect() as conn:
        cols = await conn.run_sync(
            lambda c: [col["name"] for col in inspect(c).get_columns("providers")]
        )
    await engine.dispose()
    assert {
        "id",
        "name",
        "type",
        "kind",
        "config_json",
        "api_key_encrypted",
        "enabled",
    } <= set(cols)
