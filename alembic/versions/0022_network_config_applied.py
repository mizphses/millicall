"""network_config.applied: 起動時再適用の可否フラグ

netd はステートレスのため、インターフェース IP・nftables・dnsmasq 設定は
OS/コンテナ再起動で消える。core 起動時に保存済みネットワーク設定を自動再適用
できるようにするため、「一度でも apply に成功したか」を示す applied 列を追加する。

  - False（既定）: 一度も適用していない。起動時に再適用しない
                   （デフォルト設定のまま勝手に enp3s0 を掴むのを防ぐ）。
  - True         : POST /api/network/apply が成功済み。起動時に再適用する。

既存行（マイグレーション適用前から存在する network_config）は server_default
により False で埋まる。次回 apply 成功時に True へ更新される。

Revision ID: 0022
Revises: 0021
Create Date: 2026-07-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "network_config",
        sa.Column("applied", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("network_config", "applied")
