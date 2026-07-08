"""auth_throttle: login_attempts テーブル追加（レート制限・ロックアウト用）

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """ログイン失敗試行を記録する login_attempts テーブルを作成する。

    - key       : レート制限のキー（IP アドレスまたはユーザー名）
    - key_type  : "ip" または "username"
    - ip_address: リクエスト元 IP（参考情報）
    - username  : 試行対象ユーザー名（参考情報）
    - action    : "login" / "totp" など（どのエンドポイントで失敗したか）
    - created_at: タイムスタンプ（ウィンドウ計算に使用）

    窓内のカウントは SELECT COUNT WHERE created_at > now - window で行う。
    古いレコードは定期的に DELETE できるが、少量のため運用上は任意。
    """
    op.create_table(
        "login_attempts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("key", sa.String(255), nullable=False),
        sa.Column("key_type", sa.String(20), nullable=False),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("username", sa.String(100), nullable=True),
        sa.Column("action", sa.String(30), nullable=False, server_default="login"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    # key + created_at を複合インデックス: ウィンドウ集計クエリを高速化
    op.create_index("ix_login_attempts_key_created_at", "login_attempts", ["key", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_login_attempts_key_created_at", table_name="login_attempts")
    op.drop_table("login_attempts")
