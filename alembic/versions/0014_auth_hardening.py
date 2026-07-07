"""auth_hardening: User拡張(email/enabled/external_id/session_epoch) + audit_logs

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-08
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # users テーブルへのカラム追加。
    op.add_column("users", sa.Column("email", sa.String(255), nullable=True))
    op.create_index("ix_users_email", "users", ["email"], unique=False)
    op.add_column(
        "users",
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column("users", sa.Column("external_id", sa.String(255), nullable=True))
    op.add_column(
        "users",
        sa.Column("session_epoch", sa.Integer(), nullable=False, server_default="0"),
    )
    # totp_secret を String(64) → String(255) に拡張。
    # SQLite は ALTER COLUMN TYPE を持たないため batch モード（テーブル再作成）で行う。
    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column(
            "totp_secret", existing_type=sa.String(64), type_=sa.String(255)
        )

    # audit_logs テーブル作成。
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("actor_label", sa.String(100), nullable=False),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("target_type", sa.String(50), nullable=True),
        sa.Column("target_id", sa.String(64), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"], unique=False)
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"], unique=False)


def downgrade() -> None:
    # audit_logs テーブル削除（先に外部キー参照元を削除）。
    op.drop_index("ix_audit_logs_created_at", table_name="audit_logs")
    op.drop_index("ix_audit_logs_action", table_name="audit_logs")
    op.drop_table("audit_logs")

    # users カラム削除・totp_secret を元のサイズに戻す。
    # SQLite 互換のため batch モード（テーブル再作成）で行う。
    op.drop_index("ix_users_email", table_name="users")
    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column(
            "totp_secret", existing_type=sa.String(255), type_=sa.String(64)
        )
        batch_op.drop_column("session_epoch")
        batch_op.drop_column("external_id")
        batch_op.drop_column("enabled")
        batch_op.drop_column("email")
