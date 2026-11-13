"""Webhook（issue #121）: 署名・イベント変換・配信/再試行・API。"""

import hashlib
import hmac
import json
import typing

import httpx
import pytest
from sqlalchemy import func, select

from millicall.crypto import SecretBox
from millicall.models import AuditLog, Webhook, WebhookDelivery
from millicall.webhooks import dispatcher as dispatcher_mod
from millicall.webhooks.dispatcher import (
    DELIVERY_HEADER,
    EVENT_HEADER,
    SIGNATURE_HEADER,
    WEBHOOK_EVENTS,
    WebhookDispatcher,
    compute_signature,
    event_to_webhook,
)
from millicall.webhooks.router import WebhookEvent

# --------------------------------------------------------------------------- 純関数


def test_compute_signature_known_value():
    body = b'{"event":"webhook.test"}'
    assert (
        compute_signature("s3cret", body)
        == "sha256=5ccb9c0177e5c3a531ec1e862fb7e491767a9e39e4f3c3c576dd0df90d44ed3a"
    )


def test_event_literal_matches_dispatcher_events():
    assert set(typing.get_args(WebhookEvent)) == set(WEBHOOK_EVENTS)


def _channel_create(**kw) -> dict[str, str]:
    ev = {
        "Event-Name": "CHANNEL_CREATE",
        "Call-Direction": "inbound",
        "Unique-ID": "a1b2c3d4-0000-1111-2222-333344445555",
        "Channel-Call-UUID": "a1b2c3d4-0000-1111-2222-333344445555",
        "Channel-Name": "sofia/external_Flets01/0701234567@192.168.1.1",
        "Caller-Caller-ID-Number": "0701234567",
        "Caller-Caller-ID-Name": "TARO",
        "Caller-Destination-Number": "0445895782",
    }
    ev.update(kw)
    return ev


def test_call_inbound_payload_from_channel_create():
    name, data = event_to_webhook(_channel_create())
    assert name == "call.inbound"
    assert data == {
        "call": {
            "uuid": "a1b2c3d4-0000-1111-2222-333344445555",
            "trunk": "Flets01",
            "from": "0701234567",
            "caller_name": "TARO",
            "to": "0445895782",
        }
    }


def test_channel_create_from_internal_or_outbound_is_ignored():
    # 内線電話機（internal プロファイル）の発信
    assert event_to_webhook(_channel_create(**{"Channel-Name": "sofia/internal/1001@x"})) is None
    # トランク宛の B レグ（outbound）
    assert event_to_webhook(_channel_create(**{"Call-Direction": "outbound"})) is None


def test_trunk_falls_back_to_sofia_profile_variable():
    ev = _channel_create(
        **{"Channel-Name": "loopback/x", "variable_sofia_profile_name": "external_my050"}
    )
    assert event_to_webhook(ev)[1]["call"]["trunk"] == "my050"


def _hangup(unique_id: str, call_uuid: str) -> dict[str, str]:
    return {
        "Event-Name": "CHANNEL_HANGUP_COMPLETE",
        "Unique-ID": unique_id,
        "Channel-Call-UUID": call_uuid,
        "Call-Direction": "inbound",
        "Channel-Name": "sofia/external_Flets01/0701234567@192.168.1.1",
        "Caller-Caller-ID-Number": "0701234567",
        "Caller-Destination-Number": "0445895782",
        "Caller-Channel-Created-Time": "1700000000000000",
        "Caller-Channel-Answered-Time": "1700000005000000",
        "Caller-Channel-Hangup-Time": "1700000065000000",
        "variable_duration": "65",
        "variable_billsec": "60",
        "Hangup-Cause": "NORMAL_CLEARING",
    }


def test_call_ended_payload_matches_cdr_fields():
    name, data = event_to_webhook(_hangup("u-1", "u-1"))
    assert name == "call.ended"
    call = data["call"]
    assert call["uuid"] == "u-1"
    assert call["trunk"] == "Flets01"
    assert call["duration_seconds"] == 65
    assert call["billsec_seconds"] == 60
    assert call["hangup_cause"] == "NORMAL_CLEARING"
    assert call["started_at"] == "2023-11-14T22:13:20Z"


def test_call_ended_b_leg_is_ignored():
    assert event_to_webhook(_hangup("b-leg", "a-leg")) is None


# --------------------------------------------------------------------------- 配信


class _Recorder:
    def __init__(self, statuses: list[int]):
        self.statuses = statuses
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        code = self.statuses[min(len(self.requests) - 1, len(self.statuses) - 1)]
        return httpx.Response(code, text="ok")

    async def factory(self, url: str) -> httpx.AsyncBaseTransport:
        return httpx.MockTransport(self.handler)


def _box(app) -> SecretBox:
    return SecretBox(app.state.secrets.master_key)


async def _add_hook(app, *, events, secret="", url="http://hooks.example/recv", enabled=True):
    async with app.state.sessionmaker() as s:
        hook = Webhook(
            name="h",
            url=url,
            secret_encrypted=_box(app).encrypt(secret) if secret else "",
            events=json.dumps(events),
            enabled=enabled,
        )
        s.add(hook)
        await s.commit()
        return hook.id


def _dispatcher(app, recorder: _Recorder) -> WebhookDispatcher:
    return WebhookDispatcher(
        app.state.sessionmaker,
        _box(app),
        transport_factory=recorder.factory,
        retry_delays=(0.0, 0.0),
    )


async def test_dispatcher_filters_by_subscribed_events_and_signs(app):
    inbound_id = await _add_hook(app, events=["call.inbound"], secret="topsecret")
    await _add_hook(app, events=["fax.received"])
    await _add_hook(app, events=["call.inbound"], enabled=False)
    rec = _Recorder([200])
    d = _dispatcher(app, rec)

    await d.handle(_channel_create())
    await d.drain()

    assert len(rec.requests) == 1
    req = rec.requests[0]
    body = req.content
    payload = json.loads(body)
    assert payload["event"] == "call.inbound"
    assert payload["call"]["trunk"] == "Flets01"
    assert req.headers[EVENT_HEADER] == "call.inbound"
    assert req.headers[DELIVERY_HEADER] == payload["id"]
    expected = "sha256=" + hmac.new(b"topsecret", body, hashlib.sha256).hexdigest()
    assert req.headers[SIGNATURE_HEADER] == expected
    async with app.state.sessionmaker() as s:
        rows = (await s.scalars(select(WebhookDelivery))).all()
    assert [(r.webhook_id, r.success, r.status_code, r.attempt) for r in rows] == [
        (inbound_id, True, 200, 1)
    ]


async def test_unsigned_webhook_has_no_signature_header(app):
    await _add_hook(app, events=["call.inbound"])
    rec = _Recorder([204])
    d = _dispatcher(app, rec)
    await d.handle(_channel_create())
    await d.drain()
    assert SIGNATURE_HEADER not in rec.requests[0].headers


async def test_failed_delivery_is_retried_three_times_and_logged(app):
    hook_id = await _add_hook(app, events=["call.ended"])
    rec = _Recorder([500])
    d = _dispatcher(app, rec)
    await d.handle(_hangup("u-9", "u-9"))
    await d.drain()

    assert len(rec.requests) == 3
    # 再試行でも Delivery ID は同じ（受信側の冪等化に使える）
    assert len({r.headers[DELIVERY_HEADER] for r in rec.requests}) == 1
    async with app.state.sessionmaker() as s:
        rows = (
            await s.scalars(select(WebhookDelivery).order_by(WebhookDelivery.id))
        ).all()
    assert [r.attempt for r in rows] == [1, 2, 3]
    assert all(r.webhook_id == hook_id and not r.success and r.status_code == 500 for r in rows)
    assert rows[0].error == "HTTP 500"


async def test_retry_then_success_stops(app):
    await _add_hook(app, events=["call.ended"])
    rec = _Recorder([503, 200])
    d = _dispatcher(app, rec)
    await d.handle(_hangup("u-9", "u-9"))
    await d.drain()
    assert len(rec.requests) == 2


async def test_ssrf_blocked_url_is_logged_without_request(app):
    hook_id = await _add_hook(app, events=["call.inbound"], url="http://169.254.169.254/x")
    d = WebhookDispatcher(app.state.sessionmaker, _box(app), retry_delays=(0.0, 0.0))
    await d.handle(_channel_create())
    await d.drain()
    async with app.state.sessionmaker() as s:
        rows = (await s.scalars(select(WebhookDelivery))).all()
    assert len(rows) == 3
    assert all(r.webhook_id == hook_id and r.status_code is None for r in rows)
    assert "接続先を検証できません" in rows[0].error


async def test_redirect_is_not_followed(app):
    await _add_hook(app, events=["call.inbound"])
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(302, headers={"Location": "http://127.0.0.1/admin"})

    async def factory(url: str):
        return httpx.MockTransport(handler)

    d = WebhookDispatcher(
        app.state.sessionmaker, _box(app), transport_factory=factory, retry_delays=(0.0, 0.0)
    )
    await d.handle(_channel_create())
    await d.drain()
    assert all("127.0.0.1" not in c for c in calls)
    assert len(calls) == 3  # 3xx は失敗扱いで再試行（追従はしない）


async def test_delivery_log_is_pruned(app, monkeypatch):
    monkeypatch.setattr(dispatcher_mod, "KEEP_DELIVERIES", 5)
    hook_id = await _add_hook(app, events=["call.inbound"])
    rec = _Recorder([200])
    d = _dispatcher(app, rec)
    for _ in range(8):
        await d.deliver(hook_id, "http://hooks.example/recv", "", "call.inbound", {})
    async with app.state.sessionmaker() as s:
        count = await s.scalar(select(func.count()).select_from(WebhookDelivery))
    assert count == 5


# --------------------------------------------------------------------------- API


@pytest.fixture
def recorder(app):
    rec = _Recorder([200])
    app.state.webhook_dispatcher = _dispatcher(app, rec)
    return rec


async def test_webhook_crud_never_returns_secret(auth_client, app):
    r = await auth_client.post(
        "/api/webhooks",
        json={
            "name": "CRM",
            "url": "https://crm.example.com/hook",
            "secret": "very-secret-value",
            "events": ["call.inbound", "fax.received", "call.inbound"],
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert "secret" not in body and "secret_encrypted" not in body
    assert body["has_secret"] is True
    assert body["events"] == ["call.inbound", "fax.received"]
    hook_id = body["id"]

    async with app.state.sessionmaker() as s:
        hook = await s.get(Webhook, hook_id)
        assert hook.secret_encrypted and "very-secret-value" not in hook.secret_encrypted
        assert _box(app).decrypt(hook.secret_encrypted) == "very-secret-value"
        audit = (await s.scalars(select(AuditLog).where(AuditLog.action == "webhook.create"))).all()
        assert len(audit) == 1 and "very-secret-value" not in (audit[0].detail or "")

    r = await auth_client.patch(
        f"/api/webhooks/{hook_id}", json={"secret": "", "enabled": False, "events": ["call.ended"]}
    )
    assert r.status_code == 200
    assert r.json()["has_secret"] is False
    assert r.json()["enabled"] is False
    assert r.json()["events"] == ["call.ended"]

    lst = await auth_client.get("/api/webhooks")
    assert [w["id"] for w in lst.json()] == [hook_id]

    r = await auth_client.delete(f"/api/webhooks/{hook_id}")
    assert r.status_code == 204
    assert (await auth_client.get(f"/api/webhooks/{hook_id}")).status_code == 404


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/x",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "http://user:pass@example.com/",
        "https://",
    ],
)
async def test_webhook_rejects_invalid_urls(auth_client, url):
    r = await auth_client.post("/api/webhooks", json={"name": "x", "url": url, "events": []})
    assert r.status_code == 422


async def test_webhook_rejects_unknown_event(auth_client):
    r = await auth_client.post(
        "/api/webhooks",
        json={"name": "x", "url": "http://a.example/", "events": ["call.secret"]},
    )
    assert r.status_code == 422


async def test_webhook_api_requires_admin(client, user_factory):
    await user_factory(username="plain", password="Passw0rd1", role="user")
    await client.post("/api/auth/login", json={"username": "plain", "password": "Passw0rd1"})
    assert (await client.get("/api/webhooks")).status_code == 403
    r = await client.post(
        "/api/webhooks", json={"name": "x", "url": "http://a.example/", "events": []}
    )
    assert r.status_code == 403


async def test_webhook_api_requires_login(client):
    assert (await client.get("/api/webhooks")).status_code == 401


async def test_test_endpoint_sends_signed_event_and_lists_delivery(auth_client, recorder):
    r = await auth_client.post(
        "/api/webhooks",
        json={"name": "t", "url": "http://hooks.example/recv", "secret": "k", "events": []},
    )
    hook_id = r.json()["id"]
    r = await auth_client.post(f"/api/webhooks/{hook_id}/test")
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["success"] is True and result["status_code"] == 200

    req = recorder.requests[0]
    assert req.headers[EVENT_HEADER] == "webhook.test"
    assert req.headers[SIGNATURE_HEADER] == compute_signature("k", req.content)
    assert json.loads(req.content)["test"]["webhook_id"] == hook_id

    deliveries = await auth_client.get(f"/api/webhooks/{hook_id}/deliveries")
    assert deliveries.status_code == 200
    rows = deliveries.json()
    assert len(rows) == 1
    assert rows[0]["event"] == "webhook.test"
    assert rows[0]["delivery_id"] == result["delivery_id"]


async def test_test_endpoint_failure_is_single_attempt(auth_client, app):
    rec = _Recorder([500])
    app.state.webhook_dispatcher = _dispatcher(app, rec)
    r = await auth_client.post(
        "/api/webhooks", json={"name": "t", "url": "http://hooks.example/recv", "events": []}
    )
    r = await auth_client.post(f"/api/webhooks/{r.json()['id']}/test")
    assert r.json()["success"] is False
    assert r.json()["status_code"] == 500
    assert len(rec.requests) == 1


async def test_deliveries_404_for_missing_webhook(auth_client):
    assert (await auth_client.get("/api/webhooks/999/deliveries")).status_code == 404
    assert (await auth_client.post("/api/webhooks/999/test")).status_code == 404


@pytest.mark.parametrize(("status", "expected"), [(400, 1), (401, 1), (404, 1), (408, 3), (429, 3)])
async def test_4xx_is_not_retried_except_408_429(app, status, expected):
    await _add_hook(app, events=["call.ended"])
    rec = _Recorder([status])
    d = _dispatcher(app, rec)
    await d.handle(_hangup("u-9", "u-9"))
    await d.drain()
    assert len(rec.requests) == expected
