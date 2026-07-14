"""trunks.trunk_type / inbound_cidrs: インターネット SIP プロバイダ対応

外線トランクを HGW（NTT フレッツ光・LAN 内）とインターネット越しの SIP プロバイダ
（Brastel my050 等）で切り替えられるようにするための 2 カラムを追加する。

  - trunk_type   : "hgw"（既定・既存挙動を維持）/ "sip"（インターネット SIP）
  - inbound_cidrs: SIP 種別の着信許可 CIDR（カンマ区切り）。空 = ACL を掛けない。

既存行は server_default により trunk_type="hgw" / inbound_cidrs="" となり挙動は不変。

Revision ID: 0023
Revises: 0022
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "trunks",
        sa.Column(
            "trunk_type",
            sa.String(length=10),
            nullable=False,
            server_default="hgw",
        ),
    )
    op.add_column(
        "trunks",
        sa.Column(
            "inbound_cidrs",
            sa.String(length=255),
            nullable=False,
            server_default="",
        ),
    )


def downgrade() -> None:
    op.drop_column("trunks", "inbound_cidrs")
    op.drop_column("trunks", "trunk_type")
