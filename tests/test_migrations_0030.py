"""マイグレーション 0030（Webhook の本文形式）の upgrade/downgrade テスト。"""

import sqlite3

from alembic.command import downgrade, upgrade
from alembic.config import Config


def _cfg(tmp_path):
    cfg = Config()
    cfg.set_main_option("script_location", "alembic")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{tmp_path}/test.db")
    return cfg, str(tmp_path / "test.db")


def _cols(con) -> set[str]:
    return {row[1] for row in con.execute("PRAGMA table_info(webhooks)")}


def test_migration_0030_existing_webhooks_become_generic(tmp_path):
    cfg, db_path = _cfg(tmp_path)
    upgrade(cfg, "0029")
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO webhooks (name, url, events)"
        " VALUES ('h', 'https://a.example/', '[\"call.inbound\"]')"
    )
    con.commit()
    assert "format" not in _cols(con)

    upgrade(cfg, "0030")
    row = con.execute("SELECT name, url, events, format FROM webhooks").fetchone()
    con.close()
    assert row == ("h", "https://a.example/", '["call.inbound"]', "generic")


def test_migration_0030_downgrade_removes_column(tmp_path):
    cfg, db_path = _cfg(tmp_path)
    upgrade(cfg, "0030")
    downgrade(cfg, "0029")
    con = sqlite3.connect(db_path)
    cols = _cols(con)
    con.close()
    assert "format" not in cols
    assert {"name", "url", "events", "enabled"} <= cols
