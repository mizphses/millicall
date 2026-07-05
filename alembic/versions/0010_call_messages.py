"""call_messages

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-05
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "call_messages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("call_uuid", sa.String(length=150), nullable=False),
        sa.Column("agent_id", sa.Integer(), nullable=True),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_call_messages_call_uuid", "call_messages", ["call_uuid"])


def downgrade() -> None:
    op.drop_index("ix_call_messages_call_uuid", table_name="call_messages")
    op.drop_table("call_messages")
