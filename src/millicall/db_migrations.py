from pathlib import Path

from alembic import command
from alembic.config import Config

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_ALEMBIC_INI = _REPO_ROOT / "alembic.ini"
_SCRIPT_LOCATION = _REPO_ROOT / "alembic"


# alembic/env.py・0027 と共有する Config.attributes のキー。
MASTER_KEY_ATTR = "millicall_master_key"
# マイグレーションが立てるフラグ。env.py がトランザクション終了後に VACUUM する。
VACUUM_FLAG_ATTR = "millicall_vacuum_after_migration"


def _config(database_url: str, master_key: str | None = None) -> Config:
    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("script_location", str(_SCRIPT_LOCATION))
    cfg.set_main_option("sqlalchemy.url", database_url)
    if master_key:
        # 鍵は ini/環境変数ではなくプロセス内の attributes だけで渡す（ログ・ファイルに残さない）。
        cfg.attributes[MASTER_KEY_ATTR] = master_key
    return cfg


def upgrade_to_head(database_url: str, master_key: str | None = None) -> None:
    """DB を head へ上げる。

    master_key は SIP 認証情報の暗号化マイグレーション（0027）で使う。暗号化すべき行が
    あるのに鍵が無い場合、マイグレーションは失敗する（fail-closed）。
    """
    command.upgrade(_config(database_url, master_key), "head")
