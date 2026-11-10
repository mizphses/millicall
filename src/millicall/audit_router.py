"""監査ログ参照 API。管理者専用。"""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from millicall.deps import get_session, require_admin
from millicall.models import AuditLog, User
from millicall.query_filters import validate_period

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
    q: str = Query(default="", max_length=200),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    order: Literal["newest", "oldest"] = Query(default="newest"),
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> list[AuditLog]:
    """監査ログ一覧を取得する（管理者専用、新しい順）。"""
    since, until = validate_period(since, until)
    stmt = select(AuditLog)
    if q.strip():
        stmt = stmt.where(or_(*(
            column.icontains(q.strip(), autoescape=True)
            for column in (
                AuditLog.actor_label, AuditLog.action, AuditLog.target_type,
                AuditLog.target_id, AuditLog.detail, AuditLog.ip_address,
                AuditLog.target_type + "#" + AuditLog.target_id,
            )
        )))
    if since is not None:
        stmt = stmt.where(AuditLog.created_at >= since)
    if until is not None:
        stmt = stmt.where(AuditLog.created_at < until)
    if order == "oldest":
        stmt = stmt.order_by(AuditLog.created_at.asc(), AuditLog.id.asc())
    else:
        stmt = stmt.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
    result = await session.execute(stmt.limit(limit).offset(offset))
    return list(result.scalars().all())
