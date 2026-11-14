"""trunks: register_expire_seconds（gateway の REGISTER 更新間隔）

issue #132。external_trunk テンプレートは expire-seconds を 3600 固定にしていたため、
OPTIONS ping を 0 にした NAT 内のインターネット SIP（Brastel 050 等）では再 REGISTER
以外に NAT の穴を維持する通信が無く、HGW の UDP NAT エントリが閉じて事業者からの
INVITE が届かなくなっていた。トランクごとに更新間隔を設定可能にする。

  - register_expire_seconds : Integer NOT NULL、既定 3600（既存行も 3600 = 従来挙動を維持）

Revision ID: 0031
Revises: 0030
Create Date: 2026-11-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("trunks") as batch_op:
        batch_op.add_column(
            sa.Column(
                "register_expire_seconds",
                sa.Integer(),
                nullable=False,
                server_default="3600",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("trunks") as batch_op:
        batch_op.drop_column("register_expire_seconds")
