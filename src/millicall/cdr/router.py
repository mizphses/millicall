from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from millicall.cdr.schemas import CdrRead
from millicall.deps import get_session, require_admin
from millicall.models import Cdr
from millicall.query_filters import validate_period

router = APIRouter(prefix="/api/cdr", tags=["cdr"], dependencies=[Depends(require_admin)])


@router.get("", response_model=list[CdrRead])
async def list_cdr(
    session: AsyncSession = Depends(get_session),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    direction: str | None = Query(default=None),
    q: str = Query(default="", max_length=200),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    order: Literal["newest", "oldest"] = Query(default="newest"),
) -> list[Cdr]:
    since, until = validate_period(since, until)
    stmt = select(Cdr)
    if order == "oldest":
        stmt = stmt.order_by(Cdr.started_at.asc(), Cdr.id.asc())
    else:
        stmt = stmt.order_by(Cdr.started_at.desc(), Cdr.id.desc())
    if direction is not None:
        stmt = stmt.where(Cdr.direction == direction)
    if q.strip():
        stmt = stmt.where(or_(
            Cdr.src_number.icontains(q.strip(), autoescape=True),
            Cdr.dst_number.icontains(q.strip(), autoescape=True),
            Cdr.caller_id_name.icontains(q.strip(), autoescape=True),
            Cdr.call_uuid.icontains(q.strip(), autoescape=True),
        ))
    if since is not None:
        stmt = stmt.where(Cdr.started_at >= since)
    if until is not None:
        stmt = stmt.where(Cdr.started_at < until)
    stmt = stmt.offset(offset).limit(limit)
    result = await session.scalars(stmt)
    return list(result)



@router.get("/{cdr_id}", response_model=CdrRead)
async def get_cdr(cdr_id: int, session: AsyncSession = Depends(get_session)) -> Cdr:
    cdr = await session.get(Cdr, cdr_id)
    if cdr is None:
        raise HTTPException(status_code=404, detail="通話履歴が見つかりません")
    return cdr
