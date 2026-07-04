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
) -> list[Cdr]:
    result = await session.scalars(
        select(Cdr).order_by(Cdr.started_at.desc(), Cdr.id.desc()).limit(limit)
    )
    return list(result)
