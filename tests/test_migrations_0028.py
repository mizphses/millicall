"""マイグレーション 0028（trunks.options_ping_seconds）の upgrade/downgrade テスト。"""

import sqlite3

from alembic.command import downgrade, upgrade
from alembic.config import Config


def _cfg(tmp_path):
    cfg = Config()
    cfg.set_main_option("script_location", "alembic")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{tmp_path}/test.db")
    return cfg, str(tmp_path / "test.db")


def _cols(db_path: str) -> set[str]:
    con = sqlite3.connect(db_path)
    try:
        return {row[1] for row in con.execute("PRAGMA table_info(trunks)")}
    finally:
        con.close()


def test_migration_0028_existing_trunks_default_to_30(tmp_path):
    cfg, db_path = _cfg(tmp_path)
    upgrade(cfg, "0027")
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO trunks (name, display_name, host, username, password_encrypted)"
        " VALUES ('hgw', 'HGW', '192.168.1.1', '0312345678', '')"
    )
    con.commit()
    con.close()
    assert "options_ping_seconds" not in _cols(db_path)

    upgrade(cfg, "0028")
    con = sqlite3.connect(db_path)
    row = con.execute("SELECT options_ping_seconds FROM trunks").fetchone()
    con.close()
    assert row == (30,)


def test_migration_0028_downgrade_removes_column(tmp_path):
    cfg, db_path = _cfg(tmp_path)
    upgrade(cfg, "0028")
    downgrade(cfg, "0027")
    cols = _cols(db_path)
    assert "options_ping_seconds" not in cols
    assert "password_encrypted" in cols
