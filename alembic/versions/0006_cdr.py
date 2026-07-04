"""cdr

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-05
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cdr",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("call_uuid", sa.String(length=150), nullable=False),
        sa.Column("direction", sa.String(length=20), nullable=False, server_default=""),
        sa.Column("src_number", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("dst_number", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("caller_id_name", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("answered_at", sa.DateTime(), nullable=True),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("billsec_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("hangup_cause", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("call_uuid"),
    )


def downgrade() -> None:
    op.drop_table("cdr")
