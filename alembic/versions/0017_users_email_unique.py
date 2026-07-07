"""users.email に UNIQUE 制約（レビュー M-1: SAML/SCIM の email 照合の曖昧さ解消）

非 UNIQUE の index を UNIQUE index に張り替える。SQLite の UNIQUE index は複数 NULL を
許容するため、email 未設定（ローカル既定 admin 等）は影響を受けない。

Revision ID: 0017
Revises: 0016
Create Date: 2026-07-08
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_users_email", table_name="users")
    op.create_index("ix_users_email", "users", ["email"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_users_email", table_name="users")
    op.create_index("ix_users_email", "users", ["email"], unique=False)
