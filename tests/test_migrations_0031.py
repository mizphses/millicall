"""マイグレーション 0031（trunks.register_expire_seconds）の upgrade/downgrade テスト。"""

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


def test_migration_0031_existing_trunks_default_to_3600(tmp_path):
    cfg, db_path = _cfg(tmp_path)
    upgrade(cfg, "0030")
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO trunks (name, display_name, host, username, password_encrypted,"
        " options_ping_seconds) VALUES ('brastel', 'Brastel', 'h', 'u', '', 0)"
    )
    con.commit()
    assert "register_expire_seconds" not in _cols(con)

    upgrade(cfg, "0031")
    row = con.execute(
        "SELECT name, options_ping_seconds, register_expire_seconds FROM trunks"
    ).fetchone()
    con.close()
    assert row == ("brastel", 0, 3600)


def test_migration_0031_downgrade_removes_column(tmp_path):
    cfg, db_path = _cfg(tmp_path)
    upgrade(cfg, "0031")
    downgrade(cfg, "0030")
    con = sqlite3.connect(db_path)
    cols = _cols(con)
    con.close()
    assert "register_expire_seconds" not in cols
    assert {"name", "options_ping_seconds", "password_encrypted"} <= cols
