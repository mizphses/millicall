"""統一内線番号プラン: ring_groups + ai_agents.number + trunks.inbound_extension、routes 廃止

「番号を持つものはすべて内線」モデルへの移行:
  - ring_groups / ring_group_members: グループ着信（一斉鳴動）
  - ai_agents.number: AI エージェントの内線番号（任意）
  - trunks.inbound_extension: トランク着信の転送先内線番号（空 = 着信を受けない）
  - routes テーブルを削除。既存の有効な route は以下の規則で変換する:
      * match_number がいずれかの trunk の username / did_number に一致する場合のみ変換
        （HGW は着信 INVITE の destination_number にトランクの内線登録番号を載せるため、
         一致する route はそのトランクの着信定義とみなせる）
      * target=workflow  → trunk.inbound_extension = workflow.number
      * target=extension → trunk.inbound_extension = extension.number
      * target=ai_agent  → agent に番号が無ければ 600 番から空き番号を自動採番して付与し、
                           trunk.inbound_extension = その番号
      * どの trunk にも一致しない route は破棄（変換先が無い）

Revision ID: 0019
Revises: 0018
Create Date: 2026-07-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _pick_free_number(used: set[str], start: int = 600) -> str:
    """600 番から未使用の内線番号を探す（番号プラン全体の used に対して）。"""
    n = start
    while str(n) in used:
        n += 1
    return str(n)


def upgrade() -> None:
    # --- 新テーブル / カラム ---
    op.create_table(
        "ring_groups",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("number", sa.String(20), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("number"),
    )
    op.create_table(
        "ring_group_members",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("extension_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["group_id"], ["ring_groups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["extension_id"], ["extensions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ux_ring_group_member", "ring_group_members", ["group_id", "extension_id"], unique=True
    )

    op.add_column("ai_agents", sa.Column("number", sa.String(20), nullable=True))
    op.create_index("ux_ai_agents_number", "ai_agents", ["number"], unique=True)
    op.add_column(
        "trunks",
        sa.Column("inbound_extension", sa.String(20), nullable=False, server_default=""),
    )

    # --- routes → trunks.inbound_extension / ai_agents.number 変換 ---
    conn = op.get_bind()
    trunks = conn.execute(sa.text("SELECT id, username, did_number FROM trunks")).fetchall()
    routes = conn.execute(
        sa.text("SELECT match_number, target_type, target_value FROM routes WHERE enabled = 1")
    ).fetchall()

    # 番号プラン全体の使用済み番号（AI 自動採番の衝突回避）
    used: set[str] = set()
    for row in conn.execute(sa.text("SELECT number FROM extensions")):
        used.add(row[0])
    for row in conn.execute(sa.text("SELECT number FROM workflows")):
        used.add(row[0])

    def _matching_trunk(match_number: str):
        for t in trunks:
            if match_number == t.username or (t.did_number and match_number == t.did_number):
                return t
        return None

    for r in routes:
        trunk = _matching_trunk(r.match_number)
        if trunk is None:
            continue  # 変換先が無い route は破棄
        inbound: str | None = None
        if r.target_type == "extension":
            # target_value は extension の内線番号（Phase 2 実装の契約）
            inbound = r.target_value
        elif r.target_type == "workflow":
            wf = conn.execute(
                sa.text("SELECT number FROM workflows WHERE id = :i"),
                {"i": int(r.target_value)},
            ).fetchone()
            inbound = wf[0] if wf else None
        elif r.target_type == "ai_agent":
            agent = conn.execute(
                sa.text("SELECT id, number FROM ai_agents WHERE id = :i"),
                {"i": int(r.target_value)},
            ).fetchone()
            if agent is None:
                continue
            if agent.number:
                inbound = agent.number
            else:
                inbound = _pick_free_number(used)
                used.add(inbound)
                conn.execute(
                    sa.text("UPDATE ai_agents SET number = :n WHERE id = :i"),
                    {"n": inbound, "i": agent.id},
                )
        if inbound:
            conn.execute(
                sa.text("UPDATE trunks SET inbound_extension = :n WHERE id = :i"),
                {"n": inbound, "i": trunk.id},
            )

    op.drop_table("routes")


def downgrade() -> None:
    # routes テーブルを空で復元（変換は不可逆 — 元の match_number 集合は失われている）。
    op.create_table(
        "routes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("match_number", sa.String(30), nullable=False),
        sa.Column("target_type", sa.String(20), nullable=False),
        sa.Column("target_value", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("match_number"),
    )
    op.drop_column("trunks", "inbound_extension")
    op.drop_index("ux_ai_agents_number", table_name="ai_agents")
    op.drop_column("ai_agents", "number")
    op.drop_index("ux_ring_group_member", table_name="ring_group_members")
    op.drop_table("ring_group_members")
    op.drop_table("ring_groups")
