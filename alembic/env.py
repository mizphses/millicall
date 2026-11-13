import json
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

import millicall.models  # noqa: F401  models を import して metadata を確定
from millicall.db import Base
from millicall.db_migrations import (
    MASTER_KEY_ATTR,
    VACUUM_FLAG_ATTR,
    compact_sqlite,
    sqlite_path,
)

config = context.config
if config.config_file_name is not None:
    # disable_existing_loggers=False が必須。既定 True だと、アプリ起動時に
    # upgrade_to_head() 経由でこの env.py が読み込まれた際、"millicall" を含む
    # 既存ロガーがすべて無効化され、以降アプリのログ（AI/STT/レイテンシ等）が
    # 一切出なくなる。マイグレーションのためにアプリのロギングを壊さない。
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata

# 0027 以降、SIP 認証情報の暗号化マイグレーションに master_key が必要。
# core 起動時は db_migrations.upgrade_to_head が Config.attributes に渡す。
# alembic CLI を直接使う場合は鍵そのものではなく secrets.json のパスを渡す:
#   alembic -x secrets_file=/app/data/secrets.json upgrade head


def _load_master_key_from_x_args() -> None:
    if config.attributes.get(MASTER_KEY_ATTR):
        return
    secrets_file = context.get_x_argument(as_dictionary=True).get("secrets_file")
    if not secrets_file:
        return
    data = json.loads(Path(secrets_file).read_text(encoding="utf-8"))
    config.attributes[MASTER_KEY_ATTR] = data["master_key"]


def _sync_url() -> str:
    url = config.get_main_option("sqlalchemy.url") or ""
    return url.replace("+aiosqlite", "")


def run_migrations_offline() -> None:
    context.configure(
        url=_sync_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _sync_url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    # 旧平文列の残骸（空きページ・WAL フレーム）を消す。VACUUM はトランザクション内で
    # 実行できないため、マイグレーションの接続を閉じた後に別接続で行う。失敗時は
    # data/ にマーカーを残し、次回の upgrade_to_head で再試行する。
    if config.attributes.pop(VACUUM_FLAG_ATTR, False):
        db_path = sqlite_path(_sync_url())
        if db_path is not None:
            compact_sqlite(db_path)


_load_master_key_from_x_args()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
