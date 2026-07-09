"""統一内線番号プラン。

「番号を持つものはすべて内線」— extensions / ai_agents / workflows / ring_groups が
単一の番号空間を共有する。SQLite はテーブル横断の UNIQUE を張れないため、
一意性は本モジュールの assert_number_free をすべての書き込み API が通すことで担保する。
"""

import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from millicall.models import AiAgent, Extension, RingGroup, RingGroupMember, Trunk, Workflow

# 全種別共通の内線番号形式（2〜6桁）
NUMBER_RE = re.compile(r"^\d{2,6}$")

# kind 識別子（NumberPlanEntry.kind / assert_number_free の exclude で使用）
KIND_EXTENSION = "extension"
KIND_AI_AGENT = "ai_agent"
KIND_WORKFLOW = "workflow"
KIND_RING_GROUP = "ring_group"


class NumberConflictError(ValueError):
    """番号が番号プラン内の別エンティティと重複している。"""

    def __init__(self, number: str, kind: str, label: str) -> None:
        self.number = number
        self.kind = kind
        self.label = label
        super().__init__(f"番号 {number} は既に使用されています（{kind}: {label}）")


@dataclass
class NumberPlanEntry:
    number: str
    kind: str
    id: int
    label: str
    enabled: bool
    # この番号を着信先にしているトランク名の一覧
    inbound_trunks: list[str] = field(default_factory=list)


async def list_numbers(session: AsyncSession) -> list[NumberPlanEntry]:
    """番号プラン全体の一覧を番号昇順で返す。"""
    entries: list[NumberPlanEntry] = []

    for e in await session.scalars(select(Extension)):
        entries.append(
            NumberPlanEntry(
                number=e.number,
                kind=KIND_EXTENSION,
                id=e.id,
                label=e.display_name,
                enabled=e.enabled,
            )
        )
    for a in await session.scalars(select(AiAgent).where(AiAgent.number.is_not(None))):
        entries.append(
            NumberPlanEntry(
                number=a.number or "",
                kind=KIND_AI_AGENT,
                id=a.id,
                label=a.name,
                enabled=a.enabled,
            )
        )
    for w in await session.scalars(select(Workflow)):
        entries.append(
            NumberPlanEntry(
                number=w.number,
                kind=KIND_WORKFLOW,
                id=w.id,
                label=w.name,
                enabled=w.enabled,
            )
        )
    for g in await session.scalars(select(RingGroup)):
        entries.append(
            NumberPlanEntry(
                number=g.number,
                kind=KIND_RING_GROUP,
                id=g.id,
                label=g.name,
                enabled=g.enabled,
            )
        )

    trunks = (await session.scalars(select(Trunk).where(Trunk.inbound_extension != ""))).all()
    by_number = {t.inbound_extension: [] for t in trunks}
    for t in trunks:
        by_number[t.inbound_extension].append(t.name)
    for entry in entries:
        entry.inbound_trunks = by_number.get(entry.number, [])

    entries.sort(key=lambda e: (len(e.number), e.number))
    return entries


async def find_number(session: AsyncSession, number: str) -> NumberPlanEntry | None:
    """番号プラン内で number を持つエンティティを返す（無ければ None）。"""
    e = await session.scalar(select(Extension).where(Extension.number == number))
    if e is not None:
        return NumberPlanEntry(number, KIND_EXTENSION, e.id, e.display_name, e.enabled)
    a = await session.scalar(select(AiAgent).where(AiAgent.number == number))
    if a is not None:
        return NumberPlanEntry(number, KIND_AI_AGENT, a.id, a.name, a.enabled)
    w = await session.scalar(select(Workflow).where(Workflow.number == number))
    if w is not None:
        return NumberPlanEntry(number, KIND_WORKFLOW, w.id, w.name, w.enabled)
    g = await session.scalar(select(RingGroup).where(RingGroup.number == number))
    if g is not None:
        return NumberPlanEntry(number, KIND_RING_GROUP, g.id, g.name, g.enabled)
    return None


async def assert_number_free(
    session: AsyncSession,
    number: str,
    *,
    exclude: tuple[str, int] | None = None,
) -> None:
    """number が番号プラン内で未使用であることを確認する。

    Args:
        exclude: (kind, id)。自分自身の更新時に既存の自番号を許容するために指定する。

    Raises:
        NumberConflictError: 別エンティティが同じ番号を使用している。
    """
    existing = await find_number(session, number)
    if existing is None:
        return
    if exclude is not None and (existing.kind, existing.id) == exclude:
        return
    raise NumberConflictError(number, existing.kind, existing.label)


async def load_ring_groups_with_members(
    session: AsyncSession,
) -> list[tuple[RingGroup, list[Extension]]]:
    """有効な RingGroup と、その有効メンバー内線の組を返す（dialplan 生成用）。"""
    groups = (
        await session.scalars(
            select(RingGroup).where(RingGroup.enabled.is_(True)).order_by(RingGroup.number)
        )
    ).all()
    result: list[tuple[RingGroup, list[Extension]]] = []
    for g in groups:
        members = (
            await session.scalars(
                select(Extension)
                .join(RingGroupMember, RingGroupMember.extension_id == Extension.id)
                .where(RingGroupMember.group_id == g.id, Extension.enabled.is_(True))
                .order_by(Extension.number)
            )
        ).all()
        result.append((g, list(members)))
    return result
