"""ai_agents

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_agents",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=False, server_default=""),
        sa.Column("greeting", sa.Text(), nullable=False, server_default=""),
        sa.Column("llm_provider_id", sa.Integer(), nullable=False),
        sa.Column("tts_provider_id", sa.Integer(), nullable=False),
        sa.Column("stt_provider_id", sa.Integer(), nullable=False),
        sa.Column("max_history", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("silence_end_ms", sa.Integer(), nullable=False, server_default="600"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )


def downgrade() -> None:
    op.drop_table("ai_agents")
