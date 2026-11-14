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
from millicall.webhooks.formats import (
    CHAT_FORMATS,
    WEBHOOK_FORMATS,
    build_chat_message,
    describe_event,
    format_duration,
)
from millicall.webhooks.router import WebhookEvent, WebhookFormat

# --------------------------------------------------------------------------- 純関数


def test_compute_signature_known_value():
    body = b'{"event":"webhook.test"}'
    assert (
        compute_signature("s3cret", body)
        == "sha256=5ccb9c0177e5c3a531ec1e862fb7e491767a9e39e4f3c3c576dd0df90d44ed3a"
    )


def test_event_literal_matches_dispatcher_events():
    assert set(typing.get_args(WebhookEvent)) == set(WEBHOOK_EVENTS)


def test_format_literal_matches_formats():
    assert set(typing.get_args(WebhookFormat)) == set(WEBHOOK_FORMATS)


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


async def _add_hook(
    app, *, events, secret="", url="http://hooks.example/recv", enabled=True, format="generic"
):
    async with app.state.sessionmaker() as s:
        hook = Webhook(
            name="h",
            url=url,
            secret_encrypted=_box(app).encrypt(secret) if secret else "",
            events=json.dumps(events),
            format=format,
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


# --------------------------------------------------------------------------- チャット形式（issue #130）

_OCCURRED = "2026-11-14T01:02:03Z"
_INBOUND = {
    "call": {
        "uuid": "u-1",
        "trunk": "Flets01",
        "from": "0701234567",
        "caller_name": "TARO",
        "to": "0445895782",
    }
}
_ENDED = {
    "call": {
        "uuid": "u-1",
        "direction": "inbound",
        "trunk": "Flets01",
        "from": "0701234567",
        "caller_name": "",
        "to": "0445895782",
        "started_at": "2026-11-14T01:00:00Z",
        "answered_at": "2026-11-14T01:00:05Z",
        "ended_at": "2026-11-14T01:01:10Z",
        "duration_seconds": 70,
        "billsec_seconds": 65,
        "hangup_cause": "NORMAL_CLEARING",
    }
}
_PDF = "https://pbx.example.com/api/faxes/12/pdf?token=abc"
_TEST = {"test": {"message": "millicall webhook test", "webhook_id": 7}}


def _fax(pdf_url=_PDF, success=True):
    return {
        "fax": {
            "id": 12,
            "call_uuid": "u-2",
            "from": "0312345678",
            "to": "0445895784",
            "remote_station_id": "",
            "pages": 3,
            "success": success,
            "result_text": "OK" if success else "通信エラー",
            "received_at": _OCCURRED,
            "pdf_url": pdf_url,
        }
    }


def test_chat_formats_exclude_generic():
    assert set(CHAT_FORMATS) == set(WEBHOOK_FORMATS) - {"generic"}
    with pytest.raises(ValueError):
        build_chat_message("generic", "webhook.test", _TEST, _OCCURRED)


def test_describe_event_skips_empty_values():
    msg = describe_event("call.ended", _ENDED)
    assert [f.name for f in msg.fields] == [
        "種別",
        "発信元",
        "着信先",
        "トランク",
        "通話時間",
        "切断理由",
    ]
    values = {f.name: f.value for f in msg.fields}
    assert values["種別"] == "着信"
    assert values["発信元"] == "0701234567"  # caller_name が空なら番号だけ
    assert values["通話時間"] == "1分5秒"

    unanswered = {"call": {**_ENDED["call"], "answered_at": None, "trunk": None}}
    values = {f.name: f.value for f in describe_event("call.ended", unanswered).fields}
    assert values["通話時間"] == "未応答"
    assert "トランク" not in values
    # 空の局 ID は項目にしない
    assert "送信元局 ID" not in [f.name for f in describe_event("fax.received", _fax()).fields]


def test_format_duration():
    assert format_duration(5) == "5秒"
    assert format_duration(65) == "1分5秒"
    assert format_duration(3725) == "1時間2分5秒"
    assert format_duration(None) is None


def test_discord_body_structure():
    body = build_chat_message("discord", "call.inbound", _INBOUND, _OCCURRED)
    assert "0701234567（TARO）" in body["content"]
    # 発信者名の @everyone 等でメンションしない
    assert body["allowed_mentions"] == {"parse": []}
    (embed,) = body["embeds"]
    assert embed["title"] == "📞 着信"
    assert embed["timestamp"] == _OCCURRED
    assert isinstance(embed["color"], int)
    assert {f["name"]: f["value"] for f in embed["fields"]} == {
        "発信元": "0701234567（TARO）",
        "着信先": "0445895782",
        "トランク": "Flets01",
    }
    assert all(set(f) == {"name", "value", "inline"} for f in embed["fields"])


def test_discord_fax_pdf_link_present_only_with_url():
    embed = build_chat_message("discord", "fax.received", _fax(), _OCCURRED)["embeds"][0]
    assert embed["url"] == _PDF
    values = {f["name"]: f["value"] for f in embed["fields"]}
    assert _PDF in values["PDF"]
    assert values["ページ数"] == "3 ページ"
    assert values["結果"] == "成功"

    body = build_chat_message("discord", "fax.received", _fax(None, False), _OCCURRED)
    embed = body["embeds"][0]
    assert "url" not in embed
    values = {f["name"]: f["value"] for f in embed["fields"]}
    assert "PDF" not in values
    assert values["結果"] == "失敗（通信エラー）"


def test_discord_truncates_long_values():
    data = {"call": {**_INBOUND["call"], "caller_name": "あ" * 3000}}
    body = build_chat_message("discord", "call.inbound", data, _OCCURRED)
    assert len(body["content"]) <= 2000
    assert all(len(f["value"]) <= 1024 for f in body["embeds"][0]["fields"])


def test_slack_body_structure():
    body = build_chat_message("slack", "call.ended", _ENDED, _OCCURRED)
    assert body["text"].startswith("☎️ 通話終了")
    blocks = body["blocks"]
    assert blocks[0] == {
        "type": "header",
        "text": {"type": "plain_text", "text": "☎️ 通話終了", "emoji": True},
    }
    assert blocks[-1]["type"] == "context"
    fields = next(b["fields"] for b in blocks if "fields" in b)
    assert {"type": "mrkdwn", "text": "*通話時間*\n1分5秒"} in fields
    assert {"type": "mrkdwn", "text": "*切断理由*\nNORMAL_CLEARING"} in fields


def test_slack_fax_link_and_escaping():
    body = build_chat_message("slack", "fax.received", _fax(), _OCCURRED)
    assert any(b.get("text", {}).get("text") == f"<{_PDF}|PDF を開く>" for b in body["blocks"])
    body = build_chat_message("slack", "fax.received", _fax(None), _OCCURRED)
    assert _PDF not in json.dumps(body)

    # 発信者名で <!channel> などを注入できない
    data = {"call": {**_INBOUND["call"], "caller_name": "<!channel> & co"}}
    dumped = json.dumps(build_chat_message("slack", "call.inbound", data, _OCCURRED))
    assert "<!channel>" not in dumped
    assert "&lt;!channel&gt; &amp; co" in dumped

    data = {"call": {"from": "1" * 5000}}
    assert len(build_chat_message("slack", "call.inbound", data, _OCCURRED)["text"]) <= 3000


def test_teams_body_structure():
    body = build_chat_message("teams", "fax.received", _fax(), _OCCURRED)
    assert body["type"] == "message"
    (att,) = body["attachments"]
    assert att["contentType"] == "application/vnd.microsoft.card.adaptive"
    assert att["contentUrl"] is None
    card = att["content"]
    assert card["$schema"] == "http://adaptivecards.io/schemas/adaptive-card.json"
    assert card["type"] == "AdaptiveCard"
    assert card["version"] == "1.4"
    title = card["body"][0]
    assert title["type"] == "TextBlock"
    assert title["text"] == "📠 FAX 受信"
    assert (title["weight"], title["size"]) == ("Bolder", "Medium")
    facts = next(b["facts"] for b in card["body"] if b["type"] == "FactSet")
    assert {"title": "ページ数", "value": "3 ページ"} in facts
    assert card["actions"] == [{"type": "Action.OpenUrl", "title": "PDF を開く", "url": _PDF}]

    body = build_chat_message("teams", "fax.received", _fax(None), _OCCURRED)
    assert "actions" not in body["attachments"][0]["content"]


@pytest.mark.parametrize("fmt", CHAT_FORMATS)
def test_webhook_test_message_for_each_format(fmt):
    body = build_chat_message(fmt, "webhook.test", _TEST, _OCCURRED)
    assert "Webhook テスト" in json.dumps(body, ensure_ascii=False)


_FORMAT_ROOT_KEYS = {
    "discord": {"content", "embeds", "allowed_mentions"},
    "slack": {"text", "blocks"},
    "teams": {"type", "attachments"},
}


@pytest.mark.parametrize("fmt", CHAT_FORMATS)
async def test_chat_format_delivery_is_unsigned(app, fmt):
    # シークレットが保存されていてもチャット形式では署名しない
    hook_id = await _add_hook(app, events=["call.inbound"], secret="topsecret", format=fmt)
    # Discord は 204 No Content を返す
    rec = _Recorder([204 if fmt == "discord" else 200])
    d = _dispatcher(app, rec)
    await d.handle(_channel_create())
    await d.drain()

    (req,) = rec.requests
    assert SIGNATURE_HEADER not in req.headers
    assert req.headers[EVENT_HEADER] == "call.inbound"
    assert DELIVERY_HEADER in req.headers
    assert req.headers["Content-Type"] == "application/json"
    assert set(json.loads(req.content)) == _FORMAT_ROOT_KEYS[fmt]
    async with app.state.sessionmaker() as s:
        rows = (await s.scalars(select(WebhookDelivery))).all()
    assert [(r.webhook_id, r.success) for r in rows] == [(hook_id, True)]


async def test_chat_format_ignores_undecryptable_secret(app):
    async with app.state.sessionmaker() as s:
        s.add(
            Webhook(
                name="h",
                url="http://hooks.example/recv",
                secret_encrypted="broken-token",
                events=json.dumps(["call.inbound"]),
                format="slack",
            )
        )
        await s.commit()
    rec = _Recorder([200])
    d = _dispatcher(app, rec)
    await d.handle(_channel_create())
    await d.drain()
    assert len(rec.requests) == 1


async def test_webhook_api_format_round_trip(auth_client, app):
    r = await auth_client.post(
        "/api/webhooks",
        json={"name": "g", "url": "https://crm.example.com/hook", "events": ["fax.received"]},
    )
    assert r.status_code == 201
    assert r.json()["format"] == "generic"

    r = await auth_client.post(
        "/api/webhooks",
        json={
            "name": "s",
            "url": "https://hooks.slack.com/services/T/B/x",
            "format": "slack",
            "events": ["call.inbound"],
        },
    )
    assert r.status_code == 201, r.text
    hook_id = r.json()["id"]
    assert r.json()["format"] == "slack"
    assert (await auth_client.get(f"/api/webhooks/{hook_id}")).json()["format"] == "slack"

    r = await auth_client.patch(f"/api/webhooks/{hook_id}", json={"format": "teams"})
    assert r.status_code == 200
    assert r.json()["format"] == "teams"
    # format を送らない PATCH は据え置き
    r = await auth_client.patch(f"/api/webhooks/{hook_id}", json={"name": "s2"})
    assert r.json()["format"] == "teams"

    async with app.state.sessionmaker() as s:
        audits = (
            await s.scalars(
                select(AuditLog).where(AuditLog.target_id == str(hook_id)).order_by(AuditLog.id)
            )
        ).all()
    details = [json.loads(a.detail) for a in audits]
    assert details[0]["format"] == "slack"
    assert details[1] == {"format": "teams"}


@pytest.mark.parametrize("fmt", ["chatwork", "", "Discord"])
async def test_webhook_rejects_unknown_format(auth_client, fmt):
    r = await auth_client.post(
        "/api/webhooks",
        json={"name": "x", "url": "http://a.example/", "events": [], "format": fmt},
    )
    assert r.status_code == 422
    r = await auth_client.post(
        "/api/webhooks", json={"name": "x", "url": "http://a.example/", "events": []}
    )
    r = await auth_client.patch(f"/api/webhooks/{r.json()['id']}", json={"format": fmt})
    assert r.status_code == 422


async def test_test_endpoint_uses_chat_format(auth_client, recorder):
    r = await auth_client.post(
        "/api/webhooks",
        json={
            "name": "t",
            "url": "http://hooks.example/recv",
            "secret": "k",
            "format": "discord",
            "events": [],
        },
    )
    assert r.json()["has_secret"] is True
    hook_id = r.json()["id"]
    r = await auth_client.post(f"/api/webhooks/{hook_id}/test")
    assert r.status_code == 200
    assert r.json()["success"] is True
    req = recorder.requests[0]
    assert SIGNATURE_HEADER not in req.headers
    assert req.headers[EVENT_HEADER] == "webhook.test"
    assert json.loads(req.content)["embeds"][0]["title"] == "🔔 Webhook テスト"
