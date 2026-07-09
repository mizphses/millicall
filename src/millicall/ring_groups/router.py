"""グループ着信 (ring_groups) CRUD + 番号プラン一覧 API。

統一番号プラン:
  - POST/PATCH は numberplan.assert_number_free で4テーブル横断の番号一意性を検証する。
  - 変更は TelephonyChangeListener.notify で dialplan 再生成 + reloadxml を発火する。
  - GET /api/number-plan は全種別（内線/AI/ワークフロー/グループ）の番号一覧を返す。
"""

import re

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from millicall.deps import get_change_listener, get_session, require_admin
from millicall.models import Extension, RingGroup, RingGroupMember
from millicall.numberplan import (
    KIND_RING_GROUP,
    NumberConflictError,
    assert_number_free,
    list_numbers,
)
from millicall.telephony.hooks import ExtensionChangeListener

router = APIRouter(prefix="/api", tags=["ring-groups"], dependencies=[Depends(require_admin)])

_NUMBER_RE = r"^\d{2,6}$"


class RingGroupUpsert(BaseModel):
    model_config = ConfigDict(extra="ignore")

    number: str = Field(..., pattern=_NUMBER_RE)
    name: str = Field(..., min_length=1, max_length=100)
    enabled: bool = True
    member_extension_ids: list[int] = Field(default_factory=list)


class RingGroupRead(BaseModel):
    id: int
    number: str
    name: str
    enabled: bool
    member_extension_ids: list[int]


class NumberPlanEntryRead(BaseModel):
    number: str
    kind: str  # extension | ai_agent | workflow | ring_group
    id: int
    label: str
    enabled: bool
    inbound_trunks: list[str]


async def _member_ids(session: AsyncSession, group_id: int) -> list[int]:
    rows = await session.scalars(
        select(RingGroupMember.extension_id).where(RingGroupMember.group_id == group_id)
    )
    return sorted(rows)


async def _validate_members(session: AsyncSession, ids: list[int]) -> None:
    if len(set(ids)) != len(ids):
        raise HTTPException(status_code=422, detail="メンバー内線が重複しています")
    for eid in ids:
        if await session.get(Extension, eid) is None:
            raise HTTPException(status_code=422, detail=f"内線 id={eid} が存在しません")


async def _set_members(session: AsyncSession, group_id: int, ids: list[int]) -> None:
    existing = await session.scalars(
        select(RingGroupMember).where(RingGroupMember.group_id == group_id)
    )
    for m in existing:
        await session.delete(m)
    # DELETE を先に flush しないと、同一メンバーを含む更新で INSERT が先に走り
    # UNIQUE 制約に当たる（SQLAlchemy の unit-of-work は順序を保証しない）
    await session.flush()
    for eid in ids:
        session.add(RingGroupMember(group_id=group_id, extension_id=eid))


async def _to_read(session: AsyncSession, g: RingGroup) -> RingGroupRead:
    return RingGroupRead(
        id=g.id,
        number=g.number,
        name=g.name,
        enabled=g.enabled,
        member_extension_ids=await _member_ids(session, g.id),
    )


@router.get("/number-plan", response_model=list[NumberPlanEntryRead])
async def get_number_plan(
    session: AsyncSession = Depends(get_session),
) -> list[NumberPlanEntryRead]:
    """統一番号プランの全エントリ（番号昇順）。"""
    return [
        NumberPlanEntryRead(
            number=e.number,
            kind=e.kind,
            id=e.id,
            label=e.label,
            enabled=e.enabled,
            inbound_trunks=e.inbound_trunks,
        )
        for e in await list_numbers(session)
    ]


@router.post("/ring-groups", response_model=RingGroupRead, status_code=status.HTTP_201_CREATED)
async def create_ring_group(
    body: RingGroupUpsert,
    session: AsyncSession = Depends(get_session),
    listener: ExtensionChangeListener = Depends(get_change_listener),
) -> RingGroupRead:
    try:
        await assert_number_free(session, body.number)
    except NumberConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    await _validate_members(session, body.member_extension_ids)
    group = RingGroup(number=body.number, name=body.name, enabled=body.enabled)
    session.add(group)
    await session.flush()
    await _set_members(session, group.id, body.member_extension_ids)
    await session.commit()
    await session.refresh(group)
    await listener.notify(session)
    return await _to_read(session, group)


@router.get("/ring-groups", response_model=list[RingGroupRead])
async def list_ring_groups(session: AsyncSession = Depends(get_session)) -> list[RingGroupRead]:
    groups = await session.scalars(select(RingGroup).order_by(RingGroup.number))
    return [await _to_read(session, g) for g in groups]


@router.get("/ring-groups/{group_id}", response_model=RingGroupRead)
async def get_ring_group(
    group_id: int, session: AsyncSession = Depends(get_session)
) -> RingGroupRead:
    g = await session.get(RingGroup, group_id)
    if g is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return await _to_read(session, g)


@router.patch("/ring-groups/{group_id}", response_model=RingGroupRead)
async def update_ring_group(
    group_id: int,
    body: RingGroupUpsert,
    session: AsyncSession = Depends(get_session),
    listener: ExtensionChangeListener = Depends(get_change_listener),
) -> RingGroupRead:
    g = await session.get(RingGroup, group_id)
    if g is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if not re.fullmatch(_NUMBER_RE, body.number):
        raise HTTPException(status_code=422, detail="番号は2〜6桁の数字で入力してください")
    try:
        await assert_number_free(session, body.number, exclude=(KIND_RING_GROUP, group_id))
    except NumberConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    await _validate_members(session, body.member_extension_ids)
    g.number = body.number
    g.name = body.name
    g.enabled = body.enabled
    await _set_members(session, g.id, body.member_extension_ids)
    await session.commit()
    await session.refresh(g)
    await listener.notify(session)
    return await _to_read(session, g)


@router.delete("/ring-groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_ring_group(
    group_id: int,
    session: AsyncSession = Depends(get_session),
    listener: ExtensionChangeListener = Depends(get_change_listener),
) -> None:
    g = await session.get(RingGroup, group_id)
    if g is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    await session.delete(g)
    await session.commit()
    await listener.notify(session)
