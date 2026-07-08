"""devices

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "devices",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("mac_address", sa.String(length=17), nullable=False),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("hostname", sa.String(length=253), nullable=True),
        sa.Column("model", sa.String(length=50), nullable=True),
        sa.Column("extension_id", sa.Integer(), nullable=True),
        sa.Column("provisioned", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("provision_token", sa.String(length=128), nullable=True),
        sa.Column("last_seen", sa.DateTime(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["extension_id"], ["extensions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("mac_address", name="uq_devices_mac_address"),
    )


def downgrade() -> None:
    op.drop_table("devices")
