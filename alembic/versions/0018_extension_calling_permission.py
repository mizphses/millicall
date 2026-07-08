"""extensions.calling_permission: 内線ごと発信権限（トールフラウド対策 §7）

発信権限ティアを extensions テーブルに追加する。
  - "internal"  : 内線のみ（国内PSTN・国際発信を禁止）
  - "domestic"  : 国内まで（国際発信を禁止）
  - "international": 国際発信を許可（global allowlist との AND 条件）

既存行のデフォルトは "domestic"（国際発信デフォルト禁止の原則に従う）。

Revision ID: 0018
Revises: 0017
Create Date: 2026-07-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "extensions",
        sa.Column(
            "calling_permission",
            sa.String(20),
            nullable=False,
            server_default="domestic",
        ),
    )


def downgrade() -> None:
    op.drop_column("extensions", "calling_permission")
