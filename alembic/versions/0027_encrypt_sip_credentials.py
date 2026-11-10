"""SIP 認証情報の暗号化保存: extensions.sip_password / trunks.password → *_encrypted

内線の SIP パスワードとトランクのパスワードを平文列から SecretBox（master_key 由来の
Fernet）で暗号化した列へ移す。平文と暗号文の混在期間は設けない（1 トランザクションで
全行を暗号化して移し、旧列を削除する）。

  - extensions.sip_password (String 64)  → extensions.sip_password_encrypted (Text, NOT NULL)
  - trunks.password (String 128)         → trunks.password_encrypted (Text, NOT NULL, 既定 "")

鍵の受け渡し: master_key は Config.attributes["millicall_master_key"] で受け取る
（core 起動時は db_migrations.upgrade_to_head が渡す。alembic CLI からは
``alembic -x secrets_file=data/secrets.json upgrade head``。env.py 参照）。
暗号化/復号すべき行があるのに鍵が無い場合は失敗する（fail-closed）。

旧列の平文は SQLite の空きページと WAL に残り得るため、env.py がこのリビジョンの
適用後（トランザクション外）に VACUUM と wal_checkpoint(TRUNCATE) を行う。完了できなければ
data/ に <db>.compact-pending マーカーを残し、次回起動時の upgrade_to_head が再試行する。

downgrade は鍵で復号して平文列へ戻す（鍵が無ければ失敗）。

Revision ID: 0027
Revises: 0023
Create Date: 2026-11-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

from millicall.crypto import SecretBox
from millicall.db_migrations import MASTER_KEY_ATTR, VACUUM_FLAG_ATTR

revision: str = "0027"
# NOTE: 0024（別セッション）・0025（#106）・0026（#107）のマージ後に down_revision を "0026" へ付け替えること。
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _box_or_fail(*, needed: bool) -> SecretBox | None:
    master_key = context.config.attributes.get(MASTER_KEY_ATTR)
    if master_key:
        return SecretBox(master_key)
    if needed:
        raise RuntimeError(
            "0027: SIP 認証情報の暗号化/復号に master_key が必要ですが渡されていません。"
            "core 起動経由で実行するか、"
            "alembic -x secrets_file=<data_dir>/secrets.json を指定してください"
        )
    return None


def _count(conn, table: str) -> int:
    return conn.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar_one()


def _add_column_if_missing(conn, table: str, column: sa.Column) -> None:
    # SQLite（pysqlite）は DML より前の DDL をトランザクション外で即時確定させるため、
    # 途中で中断すると列追加だけが残り得る。再実行で完走できるよう既存列は追加しない
    # （以降の UPDATE と batch による旧列削除は 1 トランザクションで確定する）。
    if column.name not in {c["name"] for c in sa.inspect(conn).get_columns(table)}:
        op.add_column(table, column)


def upgrade() -> None:
    conn = op.get_bind()
    needed = _count(conn, "extensions") + _count(conn, "trunks") > 0
    # 鍵の有無はスキーマ変更より先に確認する（失敗時に何も変えない）。
    box = _box_or_fail(needed=needed)

    _add_column_if_missing(
        conn, "extensions", sa.Column("sip_password_encrypted", sa.Text(), nullable=True)
    )
    _add_column_if_missing(
        conn,
        "trunks",
        sa.Column("password_encrypted", sa.Text(), nullable=False, server_default=""),
    )

    if box is not None:
        for row in conn.execute(sa.text("SELECT id, sip_password FROM extensions")).fetchall():
            conn.execute(
                sa.text("UPDATE extensions SET sip_password_encrypted = :v WHERE id = :i"),
                {"v": box.encrypt(row.sip_password), "i": row.id},
            )
        for row in conn.execute(sa.text("SELECT id, password FROM trunks")).fetchall():
            conn.execute(
                sa.text("UPDATE trunks SET password_encrypted = :v WHERE id = :i"),
                {"v": box.encrypt(row.password) if row.password else "", "i": row.id},
            )

    with op.batch_alter_table("extensions") as batch:
        batch.drop_column("sip_password")
        batch.alter_column("sip_password_encrypted", existing_type=sa.Text(), nullable=False)
    with op.batch_alter_table("trunks") as batch:
        batch.drop_column("password")

    # 旧列の平文が空きページ/WAL に残らないよう、トランザクション終了後に env.py が圧縮する。
    context.config.attributes[VACUUM_FLAG_ATTR] = True


def downgrade() -> None:
    conn = op.get_bind()
    needed = _count(conn, "extensions") + _count(conn, "trunks") > 0
    box = _box_or_fail(needed=needed)

    _add_column_if_missing(
        conn,
        "extensions",
        sa.Column("sip_password", sa.String(64), nullable=False, server_default=""),
    )
    _add_column_if_missing(
        conn, "trunks", sa.Column("password", sa.String(128), nullable=False, server_default="")
    )

    if box is not None:
        # 復号できない行があれば InvalidToken で失敗させ、平文列を空のまま残さない。
        for row in conn.execute(
            sa.text("SELECT id, sip_password_encrypted FROM extensions")
        ).fetchall():
            conn.execute(
                sa.text("UPDATE extensions SET sip_password = :v WHERE id = :i"),
                {"v": box.decrypt(row.sip_password_encrypted), "i": row.id},
            )
        for row in conn.execute(sa.text("SELECT id, password_encrypted FROM trunks")).fetchall():
            conn.execute(
                sa.text("UPDATE trunks SET password = :v WHERE id = :i"),
                {
                    "v": box.decrypt(row.password_encrypted) if row.password_encrypted else "",
                    "i": row.id,
                },
            )

    with op.batch_alter_table("extensions") as batch:
        batch.drop_column("sip_password_encrypted")
        batch.alter_column("sip_password", existing_type=sa.String(64), server_default=None)
    with op.batch_alter_table("trunks") as batch:
        batch.drop_column("password_encrypted")
        batch.alter_column("password", existing_type=sa.String(128), server_default=None)
