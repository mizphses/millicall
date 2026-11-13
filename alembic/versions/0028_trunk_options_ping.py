"""trunks: options_ping_seconds（gateway の OPTIONS キープアライブ間隔）

issue #119。external_trunk テンプレートは全 gateway に ping=30 を固定で入れていたが、
OPTIONS に応答しない事業者（Brastel 050 等）では 30 秒ごとに 408 となり、FreeSWITCH が
gateway を DOWN 扱いにして unregister → 再 REGISTER を繰り返し、登録が約 60 秒周期で
フラップしていた。トランクごとに間隔を設定可能にする（0 = ping param を出さない）。

  - options_ping_seconds : Integer NOT NULL、既定 30（既存行も 30 = 従来挙動を維持）

Revision ID: 0028
Revises: 0027
Create Date: 2026-11-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "trunks",
        sa.Column(
            "options_ping_seconds",
            sa.Integer(),
            nullable=False,
            server_default="30",
        ),
    )


def downgrade() -> None:
    with op.batch_alter_table("trunks") as batch_op:
        batch_op.drop_column("options_ping_seconds")
