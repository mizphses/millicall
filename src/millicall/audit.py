"""監査ログ記録ユーティリティ。"""
import json
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from millicall.models import AuditLog


def get_client_ip(request: Request) -> str | None:
    """リクエストからクライアントIPアドレスを取得する。"""
    return request.client.host if request.client else None


async def record_audit(
    session: AsyncSession,
    *,
    actor_user_id: int | None,
    actor_label: str,
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    detail: dict[str, Any] | str | None = None,
    ip_address: str | None = None,
) -> None:
    """監査ログを記録する。

    パスワード・トークン・秘密鍵をdetailに含めてはならない。
    detailがdictの場合はJSON文字列に変換する。
    """
    if isinstance(detail, dict):
        detail = json.dumps(detail, ensure_ascii=False)
    log_entry = AuditLog(
        actor_user_id=actor_user_id,
        actor_label=actor_label,
        action=action,
        target_type=target_type,
        target_id=target_id,
        detail=detail,
        ip_address=ip_address,
    )
    session.add(log_entry)
    # セッションの commit は呼び出し元の責務。
