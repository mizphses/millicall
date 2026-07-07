"""network_config

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-08
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "network_config",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("lan_interface", sa.String(length=20), nullable=False, server_default="enp3s0"),
        sa.Column("lan_ip", sa.String(length=45), nullable=False, server_default="172.20.0.1"),
        sa.Column("lan_prefix", sa.Integer(), nullable=False, server_default="16"),
        sa.Column("dhcp_range_start", sa.String(length=45), nullable=False, server_default="172.20.1.1"),
        sa.Column("dhcp_range_end", sa.String(length=45), nullable=False, server_default="172.20.254.254"),
        sa.Column("dhcp_lease_hours", sa.Integer(), nullable=False, server_default="12"),
        sa.Column("provisioning_base_url", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("nat_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("wan_interface", sa.String(length=20), nullable=False, server_default=""),
        sa.Column("tailscale_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("tailscale_auth_key_encrypted", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("network_config")
