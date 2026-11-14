"""trunks: dial_prefix / outbound_enabled（発信プレフィクスで発信回線を選ぶ）

issue #139。外線発信は名前順先頭のトランクに固定で、相手に表示される番号を選べなかった。
トランクごとに発信プレフィクスと「発信に使う」フラグを持たせる。

  - dial_prefix      : String(8) NOT NULL、既定 ""（既存行はプレフィクスなし = 従来挙動）
  - outbound_enabled : Boolean NOT NULL、既定 True（既存行はすべて発信候補のまま）

Revision ID: 0032
Revises: 0031
Create Date: 2026-11-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("trunks") as batch_op:
        batch_op.add_column(
            sa.Column("dial_prefix", sa.String(8), nullable=False, server_default="")
        )
        batch_op.add_column(
            sa.Column(
                "outbound_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("trunks") as batch_op:
        batch_op.drop_column("outbound_enabled")
        batch_op.drop_column("dial_prefix")
