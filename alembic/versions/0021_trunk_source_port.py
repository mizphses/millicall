"""trunks.source_port: トランクごとの送信元 SIP ポート

複数の外線トランク（SIP ゲートウェイ）を、トランクごとに別々の送信元 SIP
ポート／sofia プロファイルで REGISTER させるための列を追加する。

全トランクが同一 IP:5080 から REGISTER すると、一部の HGW が 2 本目以降に
"904 no matching challenge" を返して登録がフラップする。トランクごとに
プロファイルを分けて送信元ポートを変えることでこれを回避する。

  - None（NULL）: 自動採番（external_sip_port から +2 ずつ他ポートを避けて割当）
  - 明示値      : そのトランクは常にこのポートで REGISTER する

Revision ID: 0021
Revises: 0020
Create Date: 2026-07-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "trunks",
        sa.Column("source_port", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("trunks", "source_port")
