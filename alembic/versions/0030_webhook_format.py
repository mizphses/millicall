"""Webhook の本文形式（issue #130）

  - webhooks.format : String(20) NOT NULL、既定 "generic"（既存行は従来の millicall JSON）
                      他に "discord" / "slack" / "teams"（チャットサービス向けの本文・署名なし）

Revision ID: 0030
Revises: 0029
Create Date: 2026-11-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("webhooks") as batch_op:
        batch_op.add_column(
            sa.Column("format", sa.String(20), nullable=False, server_default="generic")
        )


def downgrade() -> None:
    with op.batch_alter_table("webhooks") as batch_op:
        batch_op.drop_column("format")
