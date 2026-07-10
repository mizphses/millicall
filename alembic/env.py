from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

import millicall.models  # noqa: F401  models を import して metadata を確定
from millicall.db import Base

config = context.config
if config.config_file_name is not None:
    # disable_existing_loggers=False が必須。既定 True だと、アプリ起動時に
    # upgrade_to_head() 経由でこの env.py が読み込まれた際、"millicall" を含む
    # 既存ロガーがすべて無効化され、以降アプリのログ（AI/STT/レイテンシ等）が
    # 一切出なくなる。マイグレーションのためにアプリのロギングを壊さない。
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


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


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
