"""マイグレーション0014のupgrade/downgradeスモークテスト。"""

from alembic.command import downgrade, upgrade
from alembic.config import Config


def test_migration_0014_upgrade_downgrade(tmp_path):
    """0014のupgrade→downgradeが正常完了することを確認する。"""
    db_url = f"sqlite:///{tmp_path}/test.db"
    cfg = Config()
    cfg.set_main_option("script_location", "alembic")
    cfg.set_main_option("sqlalchemy.url", db_url)
    upgrade(cfg, "0013")
    upgrade(cfg, "0014")
    downgrade(cfg, "0013")
