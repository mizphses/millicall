import sqlite3

from millicall.db_migrations import upgrade_to_head


def test_upgrade_creates_app_settings(tmp_path) -> None:
    db = tmp_path / "m.db"
    upgrade_to_head(f"sqlite+aiosqlite:///{db}")
    con = sqlite3.connect(db)
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        con.close()
    assert "app_settings" in tables
    assert "alembic_version" in tables


def test_upgrade_is_idempotent(tmp_path) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path / 'm.db'}"
    upgrade_to_head(url)
    upgrade_to_head(url)  # 2回目もエラーにならない
