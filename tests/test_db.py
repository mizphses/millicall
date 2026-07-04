from sqlalchemy import text

from millicall.db import create_db_engine


async def test_engine_enables_wal(tmp_path) -> None:
    engine = create_db_engine(f"sqlite+aiosqlite:///{tmp_path/'w.db'}")
    async with engine.connect() as conn:
        journal = (await conn.execute(text("PRAGMA journal_mode"))).scalar()
        fk = (await conn.execute(text("PRAGMA foreign_keys"))).scalar()
    await engine.dispose()
    assert str(journal).lower() == "wal"
    assert int(fk) == 1
