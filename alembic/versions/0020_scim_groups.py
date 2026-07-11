"""SCIM グループ永続化: scim_groups + scim_group_members

これまでインメモリ実装だった SCIM Groups を DB に永続化する。
グループの displayName は app_settings の scim_group_role_map と突合され、
origin="scim" ユーザーのロール自動付与（グループ → ロールマッピング）に使う。

Revision ID: 0020
Revises: 0019
Create Date: 2026-07-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scim_groups",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_scim_groups_display_name", "scim_groups", ["display_name"])
    op.create_table(
        "scim_group_members",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["group_id"], ["scim_groups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ux_scim_group_member", "scim_group_members", ["group_id", "user_id"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ux_scim_group_member", table_name="scim_group_members")
    op.drop_table("scim_group_members")
    op.drop_index("ix_scim_groups_display_name", table_name="scim_groups")
    op.drop_table("scim_groups")
