"""マイグレーション 0033（fax_outbox）の upgrade/downgrade テスト。"""

import sqlite3

from alembic.command import downgrade, upgrade
from alembic.config import Config


def _cfg(tmp_path):
    cfg = Config()
    cfg.set_main_option("script_location", "alembic")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{tmp_path}/test.db")
    return cfg, str(tmp_path / "test.db")


def _tables(con) -> set[str]:
    return {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_migration_0033_creates_fax_outbox_with_defaults(tmp_path):
    cfg, db_path = _cfg(tmp_path)
    upgrade(cfg, "0033")
    con = sqlite3.connect(db_path)
    con.execute("INSERT INTO fax_outbox (to_number, trunk_name) VALUES ('0312345678', 'Flets02')")
    con.commit()
    row = con.execute(
        "SELECT status, attempts, max_attempts, retry_count, pages, last_error,"
        " created_at IS NOT NULL"
        " FROM fax_outbox"
    ).fetchone()
    ddl = con.execute("SELECT sql FROM sqlite_master WHERE name='fax_outbox'").fetchone()[0]
    con.close()
    assert row == ("queued", 0, 3, 0, 0, "", 1)
    # 削除後に id を再利用しない（AUTOINCREMENT）
    assert "AUTOINCREMENT" in ddl


def test_migration_0033_downgrade_drops_table(tmp_path):
    cfg, db_path = _cfg(tmp_path)
    upgrade(cfg, "0033")
    downgrade(cfg, "0032")
    con = sqlite3.connect(db_path)
    tables = _tables(con)
    con.close()
    assert "fax_outbox" not in tables
    assert "faxes" in tables
