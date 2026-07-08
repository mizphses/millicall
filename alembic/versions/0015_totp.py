"""totp: users.totp_enabled カラム / users.recovery_codes カラム追加

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """users テーブルに TOTP 用カラムを追加する。

    - totp_enabled: TOTP 認証が確認済みで有効かを示す。
      totp_secret が存在しても verify 前は False のまま、ログインをゲートしない。
    - recovery_codes: Argon2 でハッシュされたリカバリコードの JSON 配列。
      必ず暗号化ハッシュのみを格納し、平文を格納してはならない。
    """
    # SQLite では ALTER COLUMN TYPE が使えないため batch モードを利用する。
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(
            sa.Column(
                "totp_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(sa.Column("recovery_codes", sa.Text(), nullable=True))


def downgrade() -> None:
    """追加したカラムを削除する。"""
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("recovery_codes")
        batch_op.drop_column("totp_enabled")
