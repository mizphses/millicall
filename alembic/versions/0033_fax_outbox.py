"""fax_outbox: FAX 送信ジョブ（issue #141）

管理画面からアップロードした PDF を TIFF に変換し、外線トランク経由で txfax 送信する。

  - fax_outbox : 送信ジョブ（宛先・トランク・PDF/TIFF の保存先・状態・試行回数・結果）
    status は queued / sending / sent / failed / cancelled。
    next_attempt_at は再試行の待ち合わせ時刻（NULL = すぐ送信してよい）。

Revision ID: 0033
Revises: 0032
Create Date: 2026-11-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "fax_outbox",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("to_number", sa.String(30), nullable=False),
        sa.Column("trunk_name", sa.String(50), nullable=False),
        sa.Column("pdf_path", sa.Text(), nullable=True),
        sa.Column("tiff_path", sa.Text(), nullable=True),
        sa.Column("pages", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("last_error", sa.String(255), nullable=False, server_default=""),
        sa.Column("result_text", sa.String(255), nullable=False, server_default=""),
        sa.Column("remote_station_id", sa.String(100), nullable=False, server_default=""),
        sa.Column(
            "created_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("call_uuid", sa.String(64), nullable=True),
        # 削除後に id を再利用しない（PDF ダウンロードトークンが別のジョブを開かないように）。
        sqlite_autoincrement=True,
    )
    op.create_index("ix_fax_outbox_status", "fax_outbox", ["status"])
    op.create_index("ix_fax_outbox_created_at", "fax_outbox", ["created_at"])
    op.create_index("ix_fax_outbox_call_uuid", "fax_outbox", ["call_uuid"])


def downgrade() -> None:
    op.drop_index("ix_fax_outbox_call_uuid", table_name="fax_outbox")
    op.drop_index("ix_fax_outbox_created_at", table_name="fax_outbox")
    op.drop_index("ix_fax_outbox_status", table_name="fax_outbox")
    op.drop_table("fax_outbox")
