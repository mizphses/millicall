"""監査ログ参照 API。管理者専用。"""
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from millicall.deps import get_session, require_admin
from millicall.models import AuditLog, User

router = APIRouter(prefix="/api/audit", tags=["audit"])


class AuditLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    actor_user_id: int | None
    actor_label: str
    action: str
    target_type: str | None
    target_id: str | None
    detail: str | None
    ip_address: str | None
    created_at: datetime


@router.get("", response_model=list[AuditLogRead])
async def list_audit_logs(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> list[AuditLog]:
    """監査ログ一覧を取得する（管理者専用、新しい順）。"""
    result = await session.execute(
        select(AuditLog).order_by(desc(AuditLog.created_at)).limit(limit).offset(offset)
    )
    return list(result.scalars().all())
