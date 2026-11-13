"""Webhook 配信（issue #121）。

ESL イベントストリーム（EslEventListener の _compose_handler）と FAX サービスから
イベントを受け、購読している Webhook へ HTTP POST する。

- 署名: ``X-Millicall-Signature: sha256=<HMAC-SHA256(secret, body) の hex>``
- ``X-Millicall-Event`` / ``X-Millicall-Delivery``（uuid4。再試行でも同じ値）
- タイムアウト 10 秒、最大 3 回（失敗後 1 秒 → 5 秒待って再試行）
- 送信は asyncio タスクで行い、ESL イベント処理をブロックしない（fire-and-forget）
- 接続先は net_guard.make_webhook_transport で解決・固定する。LAN（RFC1918）の受信サーバーは
  許可するが、loopback・link-local（メタデータ）・自ホストのアドレス・docker proxy / ESL の
  host:port は遮断する（ローカル専用サービスへの SSRF 防止）。リダイレクトは追わない。
- 各試行を webhook_deliveries に記録し、webhook ごとに直近 200 件だけ残す。
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from millicall.models import Webhook, WebhookDelivery
from millicall.net_guard import WebhookTargetPolicy, make_webhook_transport
from millicall.telephony.events import event_to_cdr

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from millicall.crypto import SecretBox

logger = logging.getLogger("millicall.webhooks")

EVENT_CALL_INBOUND = "call.inbound"
EVENT_CALL_ENDED = "call.ended"
EVENT_FAX_RECEIVED = "fax.received"
EVENT_TEST = "webhook.test"
# 購読可能なイベント（webhook.test は購読不要で /test からのみ送る）。
WEBHOOK_EVENTS: tuple[str, ...] = (EVENT_CALL_INBOUND, EVENT_CALL_ENDED, EVENT_FAX_RECEIVED)

SIGNATURE_HEADER = "X-Millicall-Signature"
EVENT_HEADER = "X-Millicall-Event"
DELIVERY_HEADER = "X-Millicall-Delivery"

MAX_ATTEMPTS = 3
RETRY_DELAYS: tuple[float, ...] = (1.0, 5.0)
REQUEST_TIMEOUT = 10.0
KEEP_DELIVERIES = 200
# 受信側レスポンスは読み捨てる。巨大レスポンスでメモリを食わないよう上限で打ち切る。
MAX_RESPONSE_BYTES = 64 * 1024
_ERROR_MAX = 500

# sofia プロファイル名 external_<trunk>（fsconfig のトランクごとプロファイル）。
_TRUNK_PROFILE_PREFIX = "external_"

TransportFactory = Callable[[str], Awaitable[httpx.AsyncBaseTransport]]


def compute_signature(secret: str, body: bytes) -> str:
    """``sha256=<hex>`` 形式の HMAC-SHA256 署名を返す。"""
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def utc_iso(dt: datetime | None = None) -> str:
    value = dt or datetime.now(UTC)
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def trunk_from_event(event: dict[str, str]) -> str | None:
    """チャネル名 ``sofia/external_<trunk>/...`` または sofia_profile_name からトランク名を得る。"""
    channel_name = event.get("Channel-Name", "")
    parts = channel_name.split("/")
    if len(parts) >= 2 and parts[0] == "sofia" and parts[1].startswith(_TRUNK_PROFILE_PREFIX):
        return parts[1][len(_TRUNK_PROFILE_PREFIX) :] or None
    profile = event.get("variable_sofia_profile_name", "")
    if profile.startswith(_TRUNK_PROFILE_PREFIX):
        return profile[len(_TRUNK_PROFILE_PREFIX) :] or None
    return None


def event_to_webhook(event: dict[str, str]) -> tuple[str, dict[str, Any]] | None:
    """ESL イベントを (webhook イベント名, payload 本体) に変換する。対象外は None。"""
    name = event.get("Event-Name")
    if name == "CHANNEL_CREATE":
        # トランク（external_<trunk> プロファイル）からの着信レグのみ。
        if event.get("Call-Direction") != "inbound":
            return None
        trunk = trunk_from_event(event)
        if trunk is None:
            return None
        return EVENT_CALL_INBOUND, {
            "call": {
                "uuid": event.get("Unique-ID") or event.get("Channel-Call-UUID") or "",
                "trunk": trunk,
                "from": event.get("Caller-Caller-ID-Number", ""),
                "caller_name": event.get("Caller-Caller-ID-Name", ""),
                "to": event.get("Caller-Destination-Number", ""),
            }
        }
    if name == "CHANNEL_HANGUP_COMPLETE":
        unique_id = event.get("Unique-ID", "")
        call_uuid = event.get("Channel-Call-UUID", "")
        # bridge の B レグも同じ Channel-Call-UUID で届くため、A レグ（呼の代表）だけ通知する。
        if unique_id and call_uuid and unique_id != call_uuid:
            return None
        cdr = event_to_cdr(event)
        if cdr is None:
            return None
        return EVENT_CALL_ENDED, {
            "call": {
                "uuid": cdr.call_uuid,
                "direction": cdr.direction,
                "trunk": trunk_from_event(event),
                "from": cdr.src_number,
                "caller_name": cdr.caller_id_name,
                "to": cdr.dst_number,
                "started_at": utc_iso(cdr.started_at) if cdr.started_at else None,
                "answered_at": utc_iso(cdr.answered_at) if cdr.answered_at else None,
                "ended_at": utc_iso(cdr.ended_at) if cdr.ended_at else None,
                "duration_seconds": cdr.duration_seconds,
                "billsec_seconds": cdr.billsec_seconds,
                "hangup_cause": cdr.hangup_cause,
            }
        }
    return None


def policy_transport_factory(policy: WebhookTargetPolicy | None) -> TransportFactory:
    async def factory(url: str) -> httpx.AsyncBaseTransport:
        # 同期 DNS 解決を行うためスレッドへ逃がす（issue #81）。
        _ip, transport = await asyncio.to_thread(make_webhook_transport, url, policy)
        return transport

    return factory


@dataclass
class DeliveryResult:
    delivery_id: str
    success: bool
    status_code: int | None
    error: str | None
    attempts: int


def parse_events(raw: str) -> list[str]:
    try:
        value = json.loads(raw or "[]")
    except ValueError:
        return []
    if not isinstance(value, list):
        return []
    return [e for e in value if isinstance(e, str)]


class WebhookDispatcher:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        secret_box: SecretBox,
        *,
        transport_factory: TransportFactory | None = None,
        target_policy: WebhookTargetPolicy | None = None,
        retry_delays: tuple[float, ...] = RETRY_DELAYS,
        timeout: float = REQUEST_TIMEOUT,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._box = secret_box
        self._transport_factory = transport_factory or policy_transport_factory(target_policy)
        self._retry_delays = retry_delays
        self._timeout = timeout
        self._tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------------ 入口

    async def handle(self, event: dict[str, str]) -> None:
        """ESL イベントハンドラ。対象イベントなら配信タスクを起動して即座に戻る。"""
        converted = event_to_webhook(event)
        if converted is None:
            return
        name, data = converted
        self.emit(name, data)

    def emit(self, event_name: str, data: dict[str, Any]) -> None:
        """購読中の Webhook へ非同期配信する（fire-and-forget）。"""
        self._spawn(self._dispatch(event_name, data))

    async def drain(self) -> None:
        """実行中の配信タスクの完了を待つ（停止処理・テスト用）。"""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def close(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*list(self._tasks), return_exceptions=True)
        self._tasks.clear()

    def _spawn(self, coro: Awaitable[Any]) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # ------------------------------------------------------------------ 配信

    async def _dispatch(self, event_name: str, data: dict[str, Any]) -> None:
        try:
            async with self._sessionmaker() as session:
                hooks = (
                    await session.scalars(select(Webhook).where(Webhook.enabled.is_(True)))
                ).all()
        except SQLAlchemyError:
            logger.exception("webhook 一覧の取得に失敗しました")
            return
        for hook in hooks:
            if event_name not in parse_events(hook.events):
                continue
            secret = self._decrypt_secret(hook)
            if secret is None:
                continue
            self._spawn(self.deliver(hook.id, hook.url, secret, event_name, data))

    def _decrypt_secret(self, hook: Webhook) -> str | None:
        if not hook.secret_encrypted:
            return ""
        try:
            return self._box.decrypt(hook.secret_encrypted)
        except Exception:  # noqa: BLE001 — InvalidToken 等。鍵不一致は送信しない（fail-closed）
            logger.error("webhook id=%s のシークレットを復号できません（送信をスキップ）", hook.id)
            return None

    def build_body(
        self, event_name: str, delivery_id: str, data: dict[str, Any]
    ) -> bytes:
        payload: dict[str, Any] = {
            "event": event_name,
            "id": delivery_id,
            "occurred_at": utc_iso(),
        }
        payload.update(data)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    async def deliver(
        self,
        webhook_id: int,
        url: str,
        secret: str,
        event_name: str,
        data: dict[str, Any],
        *,
        max_attempts: int = MAX_ATTEMPTS,
    ) -> DeliveryResult:
        """1 件の Webhook へ配信する（失敗時は最大 max_attempts 回まで再試行）。"""
        delivery_id = str(uuid.uuid4())
        body = self.build_body(event_name, delivery_id, data)
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "millicall-webhook/1",
            EVENT_HEADER: event_name,
            DELIVERY_HEADER: delivery_id,
        }
        if secret:
            headers[SIGNATURE_HEADER] = compute_signature(secret, body)
        status_code: int | None = None
        error: str | None = None
        attempt = 0
        for attempt in range(1, max_attempts + 1):
            status_code, error = await self._send_once(url, body, headers)
            success = status_code is not None and 200 <= status_code < 300
            await self._record(webhook_id, delivery_id, event_name, attempt, status_code, success, error)
            if success:
                return DeliveryResult(delivery_id, True, status_code, None, attempt)
            if attempt < max_attempts:
                delay = self._retry_delays[min(attempt - 1, len(self._retry_delays) - 1)]
                await asyncio.sleep(delay)
        logger.warning(
            "webhook id=%s event=%s の配信に失敗しました（%d 回試行）", webhook_id, event_name, attempt
        )
        return DeliveryResult(delivery_id, False, status_code, error, attempt)

    async def _send_once(
        self, url: str, body: bytes, headers: dict[str, str]
    ) -> tuple[int | None, str | None]:
        try:
            transport = await self._transport_factory(url)
        except ValueError as exc:
            # SSRF ブロック・DNS 解決失敗
            return None, _truncate(f"接続先を検証できません: {exc}")
        try:
            async with (
                httpx.AsyncClient(
                    transport=transport, timeout=self._timeout, follow_redirects=False
                ) as client,
                client.stream("POST", url, content=body, headers=headers) as resp,
            ):
                received = 0
                async for chunk in resp.aiter_bytes():
                    received += len(chunk)
                    if received > MAX_RESPONSE_BYTES:
                        break
                if 200 <= resp.status_code < 300:
                    return resp.status_code, None
                return resp.status_code, f"HTTP {resp.status_code}"
        except httpx.TimeoutException:
            return None, "タイムアウトしました"
        except (httpx.HTTPError, OSError, ValueError) as exc:
            return None, _truncate(f"{type(exc).__name__}: {exc}")

    async def _record(
        self,
        webhook_id: int,
        delivery_id: str,
        event_name: str,
        attempt: int,
        status_code: int | None,
        success: bool,
        error: str | None,
    ) -> None:
        try:
            async with self._sessionmaker() as session:
                session.add(
                    WebhookDelivery(
                        webhook_id=webhook_id,
                        delivery_id=delivery_id,
                        event=event_name,
                        attempt=attempt,
                        status_code=status_code,
                        success=success,
                        error=error,
                    )
                )
                await session.flush()
                cutoff = await session.scalar(
                    select(WebhookDelivery.id)
                    .where(WebhookDelivery.webhook_id == webhook_id)
                    .order_by(WebhookDelivery.id.desc())
                    .offset(KEEP_DELIVERIES - 1)
                    .limit(1)
                )
                if cutoff is not None:
                    await session.execute(
                        delete(WebhookDelivery).where(
                            WebhookDelivery.webhook_id == webhook_id,
                            WebhookDelivery.id < cutoff,
                        )
                    )
                await session.commit()
        except IntegrityError:
            # 配信中に webhook が削除された（FK 違反）。記録は不要。
            pass
        except SQLAlchemyError:
            logger.exception("webhook 配信ログの記録に失敗しました")


def _truncate(text: str) -> str:
    return text if len(text) <= _ERROR_MAX else text[: _ERROR_MAX - 1] + "…"
