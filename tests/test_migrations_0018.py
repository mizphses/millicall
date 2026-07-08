"""マイグレーション 0018 のupgrade/downgradeスモークテスト。

extensions.calling_permission カラムの追加・削除、およびデフォルト値を確認する。
"""

import sqlite3

from alembic.command import downgrade, upgrade
from alembic.config import Config


def _cfg(tmp_path):
    db_url = f"sqlite:///{tmp_path}/test.db"
    cfg = Config()
    cfg.set_main_option("script_location", "alembic")
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg, str(tmp_path / "test.db")


def test_migration_0018_upgrade_adds_column(tmp_path):
    """0018 upgrade で extensions.calling_permission カラムが追加される。"""
    cfg, db_path = _cfg(tmp_path)
    upgrade(cfg, "0017")
    # 0017 時点では calling_permission は存在しない
    con = sqlite3.connect(db_path)
    cols_before = {row[1] for row in con.execute("PRAGMA table_info(extensions)")}
    assert "calling_permission" not in cols_before

    upgrade(cfg, "0018")
    cols_after = {row[1] for row in con.execute("PRAGMA table_info(extensions)")}
    con.close()
    assert "calling_permission" in cols_after


def test_migration_0018_default_is_domestic(tmp_path):
    """0018 upgrade 後に挿入した行のデフォルト値が "domestic" であることを確認する。"""
    cfg, db_path = _cfg(tmp_path)
    upgrade(cfg, "0018")
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO extensions (number, display_name, sip_password, enabled)"
        " VALUES ('1001', 'Alice', 'pw', 1)"
    )
    con.commit()
    row = con.execute("SELECT calling_permission FROM extensions WHERE number='1001'").fetchone()
    con.close()
    assert row is not None
    assert row[0] == "domestic"


def test_migration_0018_downgrade_removes_column(tmp_path):
    """0018 downgrade で calling_permission カラムが削除される。"""
    cfg, db_path = _cfg(tmp_path)
    upgrade(cfg, "0018")
    downgrade(cfg, "0017")
    con = sqlite3.connect(db_path)
    cols = {row[1] for row in con.execute("PRAGMA table_info(extensions)")}
    con.close()
    assert "calling_permission" not in cols
