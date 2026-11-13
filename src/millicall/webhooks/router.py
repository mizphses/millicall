"""Webhook 設定 API（管理者のみ）。

シークレットは SecretBox で暗号化保存し、レスポンスには出さない（has_secret のみ）。
変更は監査ログに残す（URL は記録するがシークレットは記録しない）。
"""

import json
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from millicall.audit import get_client_ip, record_audit
from millicall.crypto import SecretBox
from millicall.deps import get_secret_box, get_session, require_admin
from millicall.models import User, Webhook, WebhookDelivery
from millicall.webhooks.dispatcher import (
    EVENT_TEST,
    WebhookDispatcher,
    parse_events,
)

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"], dependencies=[Depends(require_admin)])

# WEBHOOK_EVENTS と同じ集合（tests/test_webhooks.py で一致を検証する）。
WebhookEvent = Literal["call.inbound", "call.ended", "fax.received"]


def _validate_url(value: str) -> str:
    url = value.strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise ValueError("URL は http:// または https:// で始めてください")
    if not parts.hostname:
        raise ValueError("URL にホスト名がありません")
    if parts.username is not None or parts.password is not None:
        raise ValueError("URL に認証情報（user:pass@）を含めないでください")
    try:
        _ = parts.port
    except ValueError:
        raise ValueError("URL のポート番号が不正です") from None
    return url


class WebhookCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(..., min_length=1, max_length=100)
    url: str = Field(..., min_length=1, max_length=2048)
    # 空文字 = 署名なし。
    secret: str = Field(default="", max_length=256)
    events: list[WebhookEvent] = Field(default_factory=list)
    enabled: bool = True

    @field_validator("url")
    @classmethod
    def _check_url(cls, v: str) -> str:
        return _validate_url(v)


class WebhookUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str | None = Field(default=None, min_length=1, max_length=100)
    url: str | None = Field(default=None, min_length=1, max_length=2048)
    # 未指定 = 変更しない / "" = シークレットを削除（署名なし）/ 値 = 置き換え。
    secret: str | None = Field(default=None, max_length=256)
    events: list[WebhookEvent] | None = None
    enabled: bool | None = None

    @field_validator("url")
    @classmethod
    def _check_url(cls, v: str | None) -> str | None:
        return None if v is None else _validate_url(v)


class WebhookRead(BaseModel):
    id: int
    name: str
    url: str
    events: list[str]
    enabled: bool
    has_secret: bool
    created_at: datetime

    @classmethod
    def from_model(cls, w: Webhook) -> "WebhookRead":
        return cls(
            id=w.id,
            name=w.name,
            url=w.url,
            events=parse_events(w.events),
            enabled=w.enabled,
            has_secret=bool(w.secret_encrypted),
            created_at=w.created_at,
        )


class WebhookDeliveryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    delivery_id: str
    event: str
    attempt: int
    status_code: int | None
    success: bool
    error: str | None
    created_at: datetime


class WebhookTestResult(BaseModel):
    delivery_id: str
    success: bool
    status_code: int | None
    error: str | None


def get_webhook_dispatcher(request: Request) -> WebhookDispatcher:
    return request.app.state.webhook_dispatcher


def _dedupe(events: list[str]) -> str:
    return json.dumps(sorted(set(events)))


async def _get_or_404(session: AsyncSession, webhook_id: int) -> Webhook:
    hook = await session.get(Webhook, webhook_id)
    if hook is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return hook


@router.get("", response_model=list[WebhookRead])
async def list_webhooks(session: AsyncSession = Depends(get_session)) -> list[WebhookRead]:
    hooks = await session.scalars(select(Webhook).order_by(Webhook.id))
    return [WebhookRead.from_model(h) for h in hooks]


@router.post("", response_model=WebhookRead, status_code=status.HTTP_201_CREATED)
async def create_webhook(
    body: WebhookCreate,
    request: Request,
    session: AsyncSession = Depends(get_session),
    box: SecretBox = Depends(get_secret_box),
    user: User = Depends(require_admin),
) -> WebhookRead:
    hook = Webhook(
        name=body.name,
        url=body.url,
        secret_encrypted=box.encrypt(body.secret) if body.secret else "",
        events=_dedupe(list(body.events)),
        enabled=body.enabled,
    )
    session.add(hook)
    await session.flush()
    await record_audit(
        session,
        actor_user_id=user.id,
        actor_label=user.username,
        action="webhook.create",
        target_type="webhook",
        target_id=str(hook.id),
        detail={"name": body.name, "url": body.url, "events": sorted(set(body.events))},
        ip_address=get_client_ip(request),
    )
    await session.commit()
    await session.refresh(hook)
    return WebhookRead.from_model(hook)


@router.get("/{webhook_id}", response_model=WebhookRead)
async def get_webhook(
    webhook_id: int, session: AsyncSession = Depends(get_session)
) -> WebhookRead:
    return WebhookRead.from_model(await _get_or_404(session, webhook_id))


@router.patch("/{webhook_id}", response_model=WebhookRead)
async def update_webhook(
    webhook_id: int,
    body: WebhookUpdate,
    request: Request,
    session: AsyncSession = Depends(get_session),
    box: SecretBox = Depends(get_secret_box),
    user: User = Depends(require_admin),
) -> WebhookRead:
    hook = await _get_or_404(session, webhook_id)
    changed: dict[str, object] = {}
    if body.name is not None:
        hook.name = body.name
        changed["name"] = body.name
    if body.url is not None:
        hook.url = body.url
        changed["url"] = body.url
    if body.events is not None:
        hook.events = _dedupe(list(body.events))
        changed["events"] = sorted(set(body.events))
    if body.enabled is not None:
        hook.enabled = body.enabled
        changed["enabled"] = body.enabled
    if body.secret is not None:
        hook.secret_encrypted = box.encrypt(body.secret) if body.secret else ""
        # 値そのものは記録しない
        changed["secret"] = "updated" if body.secret else "cleared"
    await record_audit(
        session,
        actor_user_id=user.id,
        actor_label=user.username,
        action="webhook.update",
        target_type="webhook",
        target_id=str(hook.id),
        detail=changed,
        ip_address=get_client_ip(request),
    )
    await session.commit()
    await session.refresh(hook)
    return WebhookRead.from_model(hook)


@router.delete("/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_webhook(
    webhook_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_admin),
) -> None:
    hook = await _get_or_404(session, webhook_id)
    name = hook.name
    await session.delete(hook)
    await record_audit(
        session,
        actor_user_id=user.id,
        actor_label=user.username,
        action="webhook.delete",
        target_type="webhook",
        target_id=str(webhook_id),
        detail={"name": name},
        ip_address=get_client_ip(request),
    )
    await session.commit()


@router.post("/{webhook_id}/test", response_model=WebhookTestResult)
async def test_webhook(
    webhook_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
    box: SecretBox = Depends(get_secret_box),
    user: User = Depends(require_admin),
    dispatcher: WebhookDispatcher = Depends(get_webhook_dispatcher),
) -> WebhookTestResult:
    """webhook.test イベントを 1 回だけ（再試行なし）送り、結果を返す。"""
    hook = await _get_or_404(session, webhook_id)
    try:
        secret = box.decrypt(hook.secret_encrypted) if hook.secret_encrypted else ""
    except Exception:  # noqa: BLE001 — InvalidToken（master_key 不一致など）
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="シークレットを復号できません。シークレットを再設定してください",
        ) from None
    await record_audit(
        session,
        actor_user_id=user.id,
        actor_label=user.username,
        action="webhook.test",
        target_type="webhook",
        target_id=str(hook.id),
        ip_address=get_client_ip(request),
    )
    await session.commit()
    result = await dispatcher.deliver(
        hook.id,
        hook.url,
        secret,
        EVENT_TEST,
        {"test": {"message": "millicall webhook test", "webhook_id": hook.id}},
        max_attempts=1,
    )
    return WebhookTestResult(
        delivery_id=result.delivery_id,
        success=result.success,
        status_code=result.status_code,
        error=result.error,
    )


@router.get("/{webhook_id}/deliveries", response_model=list[WebhookDeliveryRead])
async def list_deliveries(
    webhook_id: int,
    session: AsyncSession = Depends(get_session),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[WebhookDelivery]:
    await _get_or_404(session, webhook_id)
    rows = await session.scalars(
        select(WebhookDelivery)
        .where(WebhookDelivery.webhook_id == webhook_id)
        .order_by(WebhookDelivery.id.desc())
        .limit(limit)
    )
    return list(rows)
