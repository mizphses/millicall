"""FAX 受信 / Webhook 通知 / トランクの FAX 自動検知（issue #121）

  - fax_receivers      : FAX 受信番号（統一番号プランの kind="fax"）
  - faxes              : 受信 FAX（rxfax 結果 + TIFF/PDF の保存先）
  - webhooks           : 通知先（secret は SecretBox 暗号化、events は JSON 配列）
  - webhook_deliveries : 配信ログ（webhook ごとに直近 200 件のみ保持はアプリ側で刈り込む）
  - trunks.fax_detect_enabled     : Boolean NOT NULL、既定 false（既存行は従来挙動）
  - trunks.fax_detect_timeout_sec : Integer NOT NULL、既定 20
  - trunks.fax_route_number       : String(20) NULL
  - trunks.fax_detect_announce    : Boolean NOT NULL、既定 false（検知中の案内音声）

Revision ID: 0029
Revises: 0028
Create Date: 2026-11-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "fax_receivers",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("number", sa.String(20), nullable=False, unique=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "faxes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("call_uuid", sa.String(150), nullable=False),
        sa.Column("from_number", sa.String(80), nullable=False, server_default=""),
        sa.Column("to_number", sa.String(80), nullable=False, server_default=""),
        sa.Column("remote_station_id", sa.String(100), nullable=False, server_default=""),
        sa.Column("pages", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("success", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("result_text", sa.String(255), nullable=False, server_default=""),
        sa.Column("tiff_path", sa.Text(), nullable=True),
        sa.Column("pdf_path", sa.Text(), nullable=True),
        sa.Column("received_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        # 削除後に id を再利用しない（PDF ダウンロードトークンが別の FAX を開かないように）。
        sqlite_autoincrement=True,
    )
    op.create_index("ix_faxes_call_uuid", "faxes", ["call_uuid"])
    op.create_index("ix_faxes_received_at", "faxes", ["received_at"])
    op.create_table(
        "webhooks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("secret_encrypted", sa.Text(), nullable=False, server_default=""),
        sa.Column("events", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "webhook_id",
            sa.Integer(),
            sa.ForeignKey("webhooks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("delivery_id", sa.String(64), nullable=False),
        sa.Column("event", sa.String(50), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_webhook_deliveries_webhook_id_id", "webhook_deliveries", ["webhook_id", "id"]
    )
    op.add_column(
        "trunks",
        sa.Column("fax_detect_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "trunks",
        sa.Column("fax_detect_timeout_sec", sa.Integer(), nullable=False, server_default="20"),
    )
    op.add_column("trunks", sa.Column("fax_route_number", sa.String(20), nullable=True))
    op.add_column(
        "trunks",
        sa.Column("fax_detect_announce", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    with op.batch_alter_table("trunks") as batch_op:
        batch_op.drop_column("fax_detect_announce")
        batch_op.drop_column("fax_route_number")
        batch_op.drop_column("fax_detect_timeout_sec")
        batch_op.drop_column("fax_detect_enabled")
    op.drop_index("ix_webhook_deliveries_webhook_id_id", table_name="webhook_deliveries")
    op.drop_table("webhook_deliveries")
    op.drop_table("webhooks")
    op.drop_index("ix_faxes_received_at", table_name="faxes")
    op.drop_index("ix_faxes_call_uuid", table_name="faxes")
    op.drop_table("faxes")
    op.drop_table("fax_receivers")
