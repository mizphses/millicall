"""shared_auth_state: MCP OAuth / SAML リプレイ状態を DB へ移す（issue #86）

これまでプロセス内 dict に保持していた MCP OAuth のクライアント登録（DCR）・認可コード・
アクセス/リフレッシュトークンと、SAML の消費済みアサーション ID / AuthnRequest ID を
SQLite に保存する。再起動で MCP の認可が失効せず、複数プロセスでも状態を共有できる。

  - mcp_oauth_clients : DCR 登録。client_secret は SHA-256 ハッシュのみ。
  - mcp_oauth_codes   : 認可コード。code は SHA-256 ハッシュのみ（DELETE ... RETURNING で一回限り消費）。
  - mcp_oauth_tokens  : access / refresh トークン。token は SHA-256 ハッシュのみ。
  - saml_consumed_ids : 消費済み SAML ID の SHA-256（PRIMARY KEY への INSERT で一回限り消費）。

時刻列はすべて UNIX 秒（REAL）。期限切れ行はアクセス時と書き込み時に機会的に削除する。
既存のインメモリ状態は移行しない（本マイグレーション適用時の再起動で一度だけ再認証が必要）。

Revision ID: 0025
Revises: 0023（マージ時に 0024 へ付け替える）
Create Date: 2026-11-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
# マージ時に main の 0024（TOTP リプレイ対策）へ付け替える: down_revision = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mcp_oauth_clients",
        sa.Column("client_id", sa.String(64), nullable=False),
        sa.Column("client_secret_hash", sa.String(64), nullable=True),
        sa.Column("client_info", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("last_used_at", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("client_id"),
    )
    op.create_index("ix_mcp_oauth_clients_created_at", "mcp_oauth_clients", ["created_at"])

    op.create_table(
        "mcp_oauth_codes",
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("client_id", sa.String(64), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("session_epoch", sa.Integer(), nullable=False),
        sa.Column("credential_tag", sa.String(64), nullable=False),
        sa.Column("code_challenge", sa.String(255), nullable=False),
        sa.Column("redirect_uri", sa.Text(), nullable=False),
        sa.Column("redirect_uri_provided_explicitly", sa.Boolean(), nullable=False),
        sa.Column("scopes", sa.Text(), nullable=False),
        sa.Column("resource", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["client_id"], ["mcp_oauth_clients.client_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("code_hash"),
    )
    op.create_index("ix_mcp_oauth_codes_client_id", "mcp_oauth_codes", ["client_id"])
    op.create_index("ix_mcp_oauth_codes_expires_at", "mcp_oauth_codes", ["expires_at"])

    op.create_table(
        "mcp_oauth_tokens",
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("grant_id", sa.String(64), nullable=False),
        sa.Column("client_id", sa.String(64), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("session_epoch", sa.Integer(), nullable=False),
        sa.Column("credential_tag", sa.String(64), nullable=False),
        sa.Column("scopes", sa.Text(), nullable=False),
        sa.Column("resource", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["client_id"], ["mcp_oauth_clients.client_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("token_hash"),
    )
    op.create_index("ix_mcp_oauth_tokens_grant_id", "mcp_oauth_tokens", ["grant_id"])
    op.create_index("ix_mcp_oauth_tokens_client_id", "mcp_oauth_tokens", ["client_id"])
    op.create_index("ix_mcp_oauth_tokens_expires_at", "mcp_oauth_tokens", ["expires_at"])
    op.create_index("ix_mcp_oauth_tokens_subject_kind", "mcp_oauth_tokens", ["subject", "kind"])

    op.create_table(
        "saml_consumed_ids",
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("key_hash"),
    )
    op.create_index("ix_saml_consumed_ids_expires_at", "saml_consumed_ids", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_saml_consumed_ids_expires_at", table_name="saml_consumed_ids")
    op.drop_table("saml_consumed_ids")
    op.drop_index("ix_mcp_oauth_tokens_subject_kind", table_name="mcp_oauth_tokens")
    op.drop_index("ix_mcp_oauth_tokens_expires_at", table_name="mcp_oauth_tokens")
    op.drop_index("ix_mcp_oauth_tokens_client_id", table_name="mcp_oauth_tokens")
    op.drop_index("ix_mcp_oauth_tokens_grant_id", table_name="mcp_oauth_tokens")
    op.drop_table("mcp_oauth_tokens")
    op.drop_index("ix_mcp_oauth_codes_expires_at", table_name="mcp_oauth_codes")
    op.drop_index("ix_mcp_oauth_codes_client_id", table_name="mcp_oauth_codes")
    op.drop_table("mcp_oauth_codes")
    op.drop_index("ix_mcp_oauth_clients_created_at", table_name="mcp_oauth_clients")
    op.drop_table("mcp_oauth_clients")
