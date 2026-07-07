import pytest
from sqlalchemy import inspect

from millicall.db import create_db_engine
from millicall.db_migrations import upgrade_to_head


@pytest.mark.asyncio
async def test_workflows_table(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'm.db'}"
    upgrade_to_head(url)
    engine = create_db_engine(url)
    async with engine.connect() as conn:
        cols = await conn.run_sync(
            lambda c: [col["name"] for col in inspect(c).get_columns("workflows")]
        )
        indexes = await conn.run_sync(
            lambda c: inspect(c).get_indexes("workflows")
        )
        uniques = await conn.run_sync(
            lambda c: inspect(c).get_unique_constraints("workflows")
        )
    await engine.dispose()
    assert {
        "id",
        "name",
        "number",
        "description",
        "default_tts_provider_id",
        "definition_json",
        "enabled",
        "created_at",
        "updated_at",
    } <= set(cols)
    # number must be unique (either via a unique index or a unique constraint)
    unique_number = any(ix.get("unique") and ix.get("column_names") == ["number"] for ix in indexes)
    unique_number = unique_number or any(
        uc.get("column_names") == ["number"] for uc in uniques
    )
    assert unique_number
