"""マイグレーション 0032（trunks.dial_prefix / outbound_enabled）の upgrade/downgrade テスト。"""

import sqlite3

from alembic.command import downgrade, upgrade
from alembic.config import Config


def _cfg(tmp_path):
    cfg = Config()
    cfg.set_main_option("script_location", "alembic")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{tmp_path}/test.db")
    return cfg, str(tmp_path / "test.db")


def _cols(con) -> set[str]:
    return {row[1] for row in con.execute("PRAGMA table_info(trunks)")}


def test_migration_0032_existing_trunks_keep_outbound(tmp_path):
    cfg, db_path = _cfg(tmp_path)
    upgrade(cfg, "0031")
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO trunks (name, display_name, host, username, password_encrypted)"
        " VALUES ('Flets01', 'Flets01', 'h', 'u', '')"
    )
    con.commit()
    assert "dial_prefix" not in _cols(con)

    upgrade(cfg, "0032")
    row = con.execute("SELECT name, dial_prefix, outbound_enabled FROM trunks").fetchone()
    con.close()
    assert row == ("Flets01", "", 1)


def test_migration_0032_downgrade_removes_columns(tmp_path):
    cfg, db_path = _cfg(tmp_path)
    upgrade(cfg, "0032")
    downgrade(cfg, "0031")
    con = sqlite3.connect(db_path)
    cols = _cols(con)
    con.close()
    assert not {"dial_prefix", "outbound_enabled"} & cols
    assert {"name", "register_expire_seconds"} <= cols
