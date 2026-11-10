"""devices: provision_mode（厳格モード）と設定ファイル取得記録

issue #85。端末固有 cfg の配信ゲートを端末ごとに選べるようにし、取得成功を記録する。

  - provision_mode               : "standard"（既定。LAN + 登録済み端末 + IP 一致 +
                                   active/enabled）/ "strict"（加えてパス形式トークン必須）
  - last_config_fetch_at         : 端末固有 cfg を最後に配信した日時（UTC, naive）
  - last_config_fetch_user_agent : そのときの User-Agent（制御文字除去・長さ制限済み）
  - last_config_fetch_used_token : そのときトークン付き URL で取得したか

既存行は provision_mode="standard" となる。従来は電話機管理者資格情報が設定済みだと
quick_provision が自動でワンタイムトークンを発行していたが、配送手段が無く、未消費の
トークンが残った端末は永久に 404 になっていた。既定をトークンなしにしたため、
残っている（＝一度も配送されなかった）旧トークンはここで破棄する。以後
provision_token には厳格モードで管理者が明示発行したトークンの SHA-256 hex を保存する。

Revision ID: 0026
Revises: 0023
Create Date: 2026-11-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
# マージ時に 0024（別セッション）/ 0025（issue #86）の後ろへ付け替える（down_revision="0025"）。
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "devices",
        sa.Column(
            "provision_mode",
            sa.String(length=16),
            nullable=False,
            server_default="standard",
        ),
    )
    op.add_column("devices", sa.Column("last_config_fetch_at", sa.DateTime(), nullable=True))
    op.add_column(
        "devices",
        sa.Column("last_config_fetch_user_agent", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "devices",
        sa.Column(
            "last_config_fetch_used_token",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    # 配送されないまま残った旧ワンタイムトークン（平文）を破棄する（上記 docstring 参照）。
    op.execute("UPDATE devices SET provision_token = NULL")


def downgrade() -> None:
    op.drop_column("devices", "last_config_fetch_used_token")
    op.drop_column("devices", "last_config_fetch_user_agent")
    op.drop_column("devices", "last_config_fetch_at")
    op.drop_column("devices", "provision_mode")
    # 0026 以降の provision_token は SHA-256 hex であり、旧コードの平文比較では一致しない。
    # 旧コードで厳格モード端末が誤って通らない（常に 404 になる）だけなので安全側だが、
    # 混乱を避けるため破棄する。
    op.execute("UPDATE devices SET provision_token = NULL")
