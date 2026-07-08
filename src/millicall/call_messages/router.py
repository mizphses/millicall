from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from millicall.call_messages.schemas import CallMessageRead
from millicall.deps import get_session, require_admin
from millicall.models import CallMessage

router = APIRouter(
    prefix="/api/call-messages", tags=["call-messages"], dependencies=[Depends(require_admin)]
)


@router.get("", response_model=list[CallMessageRead])
async def list_call_messages(
    call_uuid: str = Query(...),
    session: AsyncSession = Depends(get_session),
) -> list[CallMessage]:
    stmt = (
        select(CallMessage).where(CallMessage.call_uuid == call_uuid).order_by(CallMessage.id.asc())
    )
    result = await session.scalars(stmt)
    return list(result)
