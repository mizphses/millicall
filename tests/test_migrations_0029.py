"""マイグレーション 0029（FAX 受信 / Webhook / トランクの FAX 自動検知）の upgrade/downgrade テスト。"""

import sqlite3

from alembic.command import downgrade, upgrade
from alembic.config import Config

_NEW_TABLES = {"fax_receivers", "faxes", "webhooks", "webhook_deliveries"}
_NEW_TRUNK_COLS = {
    "fax_detect_enabled",
    "fax_detect_timeout_sec",
    "fax_route_number",
    "fax_detect_announce",
}


def _cfg(tmp_path):
    cfg = Config()
    cfg.set_main_option("script_location", "alembic")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{tmp_path}/test.db")
    return cfg, str(tmp_path / "test.db")


def _tables(con) -> set[str]:
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_migration_0029_existing_trunks_get_defaults(tmp_path):
    cfg, db_path = _cfg(tmp_path)
    upgrade(cfg, "0028")
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO trunks (name, display_name, host, username) VALUES ('t', 'T', 'h', 'u')"
    )
    con.commit()
    assert not (_NEW_TABLES & _tables(con))

    upgrade(cfg, "0029")
    assert _tables(con) >= _NEW_TABLES
    row = con.execute(
        "SELECT fax_detect_enabled, fax_detect_timeout_sec, fax_route_number,"
        " fax_detect_announce FROM trunks"
    ).fetchone()
    con.close()
    assert row == (0, 20, None, 0)


def test_migration_0029_downgrade_removes_tables_and_columns(tmp_path):
    cfg, db_path = _cfg(tmp_path)
    upgrade(cfg, "0029")
    downgrade(cfg, "0028")
    con = sqlite3.connect(db_path)
    cols = {row[1] for row in con.execute("PRAGMA table_info(trunks)")}
    tables = _tables(con)
    con.close()
    assert not (_NEW_TRUNK_COLS & cols)
    assert not (_NEW_TABLES & tables)
