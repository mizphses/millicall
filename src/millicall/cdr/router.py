from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from millicall.cdr.schemas import CdrRead
from millicall.deps import get_session, require_admin
from millicall.models import Cdr

router = APIRouter(prefix="/api/cdr", tags=["cdr"], dependencies=[Depends(require_admin)])


@router.get("", response_model=list[CdrRead])
async def list_cdr(
    session: AsyncSession = Depends(get_session),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    direction: str | None = Query(default=None),
) -> list[Cdr]:
    stmt = select(Cdr).order_by(Cdr.started_at.desc(), Cdr.id.desc())
    if direction is not None:
        stmt = stmt.where(Cdr.direction == direction)
    stmt = stmt.offset(offset).limit(limit)
    result = await session.scalars(stmt)
    return list(result)
