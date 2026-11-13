"""マイグレーション 0026（devices.provision_mode / 取得記録）の upgrade/downgrade テスト。"""

import sqlite3

from alembic.command import downgrade, upgrade
from alembic.config import Config

_NEW_COLS = {
    "provision_mode",
    "last_config_fetch_at",
    "last_config_fetch_user_agent",
    "last_config_fetch_used_token",
}


def _cfg(tmp_path):
    cfg = Config()
    cfg.set_main_option("script_location", "alembic")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{tmp_path}/test.db")
    return cfg, str(tmp_path / "test.db")


def test_migration_0026_existing_rows_become_standard_and_legacy_token_discarded(tmp_path):
    """既存行は provision_mode="standard"、配送されないまま残った旧トークンは破棄される。"""
    cfg, db_path = _cfg(tmp_path)
    upgrade(cfg, "0023")
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO devices (mac_address, ip_address, provisioned, active, provision_token)"
        " VALUES ('AA:BB:CC:DD:EE:FF', '10.0.0.50', 1, 1, 'legacy-one-time-token')"
    )
    con.commit()
    cols_before = {row[1] for row in con.execute("PRAGMA table_info(devices)")}
    assert not (_NEW_COLS & cols_before)

    upgrade(cfg, "0026")
    cols_after = {row[1] for row in con.execute("PRAGMA table_info(devices)")}
    assert cols_after >= _NEW_COLS
    row = con.execute(
        "SELECT provision_mode, provision_token, last_config_fetch_at,"
        " last_config_fetch_used_token FROM devices"
    ).fetchone()
    con.close()
    assert row == ("standard", None, None, 0)


def test_migration_0026_downgrade_removes_columns(tmp_path):
    cfg, db_path = _cfg(tmp_path)
    upgrade(cfg, "0026")
    downgrade(cfg, "0023")
    con = sqlite3.connect(db_path)
    cols = {row[1] for row in con.execute("PRAGMA table_info(devices)")}
    con.close()
    assert not (_NEW_COLS & cols)
    assert "provision_token" in cols
