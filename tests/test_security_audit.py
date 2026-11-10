"""Regression coverage for the security audit's externally reachable boundaries."""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pyotp
import pytest
from fastapi import FastAPI
from mcp.server.auth.provider import TokenError
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl
from sqlalchemy import func, select

from millicall.auth.security import hash_password
from millicall.media.audio_fork import register_media_ws
from millicall.media.call_control import EslCallControl
from millicall.media.tickets import MediaStreamTickets
from millicall.media.vad import VadSegmenter
from millicall.models import McpOAuthCode, McpOAuthToken, User
from millicall.net_guard import _is_blocked_ip, _PinnedTransport
from millicall.network.validation import is_valid_hostname, is_valid_interface
from tests.test_mcp_oauth import _form, _redirect_location, _register_client, _ticket
from tests.test_totp import _setup_and_enable_totp


async def _count(app, model, *where):
    async with app.state.sessionmaker() as db:
        return await db.scalar(select(func.count()).select_from(model).where(*where))


async def _grant(app, username):
    provider = app.state.mcp_oauth_provider
    client = OAuthClientInformationFull(
        client_id="audit",
        redirect_uris=[AnyUrl("https://client.example/callback")],
        grant_types=["authorization_code", "refresh_token"],
        token_endpoint_auth_method="none",
    )
    await provider.register_client(client)
    state = await provider._current_user_state(username)
    code = await provider.create_auth_code(
        client_id=client.client_id,
        username=username,
        code_challenge="challenge",
        redirect_uri=str(client.redirect_uris[0]),
        scopes=[],
        user_state=state[1],
    )
    authorization = await provider.load_authorization_code(client, code)
    return provider, client, authorization


async def test_disabled_password_login_issues_no_session(client, app, user_factory):
    username, password = await user_factory()
    async with app.state.sessionmaker() as db:
        user = await db.scalar(select(User).where(User.username == username))
        user.enabled = False
        await db.commit()
    response = await client.post("/api/auth/login", json=dict(username=username, password=password))
    assert response.status_code == 401
    assert app.state.settings.session_cookie_name not in response.cookies


async def test_password_step_cannot_reset_totp_failures(client, app, user_factory):
    username, password = await user_factory()
    await _setup_and_enable_totp(client, username, password)
    for _ in range(app.state.settings.login_max_attempts):
        challenge = await client.post(
            "/api/auth/login", json=dict(username=username, password=password)
        )
        assert challenge.status_code == 200
        response = await client.post(
            "/api/auth/login/totp", json=dict(ticket=challenge.json()["ticket"], code="invalid")
        )
        assert response.status_code == 401
    locked = await client.post("/api/auth/login", json=dict(username=username, password=password))
    assert locked.status_code == 429


async def test_totp_resetup_is_rate_limited(client, app, user_factory):
    username, password = await user_factory()
    await _setup_and_enable_totp(client, username, password)
    for _ in range(app.state.settings.login_max_attempts):
        assert (
            await client.post("/api/auth/totp/setup", json={"code": "invalid"})
        ).status_code == 400
    assert (await client.post("/api/auth/totp/setup", json={"code": "invalid"})).status_code == 429


async def test_totp_enrollment_revokes_previous_session(client, app, user_factory):
    username, password = await user_factory()
    await client.post("/api/auth/login", json=dict(username=username, password=password))
    old_cookie = client.cookies.get(app.state.settings.session_cookie_name)
    await _setup_and_enable_totp(client, username, password)
    assert (await client.get("/api/auth/me")).status_code == 200
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as other:
        other.cookies.set(app.state.settings.session_cookie_name, old_cookie)
        assert (await other.get("/api/auth/me")).status_code == 401


@pytest.mark.parametrize("action", ["setup", "verify", "disable"])
async def test_totp_mutation_rechecks_session_after_lock(
    client, app, user_factory, monkeypatch, action
):
    from millicall.auth import totp_router

    username, password = await user_factory()
    secret, _ = await _setup_and_enable_totp(client, username, password)
    original_check = totp_router.check_and_raise

    async def revoke_before_lock(session, **kwargs):
        # Model revocation after get_current_user read the row, before the write lock.
        async with app.state.sessionmaker() as db:
            user = await db.scalar(select(User).where(User.username == username))
            user.session_epoch += 1
            await db.commit()
        await original_check(session, **kwargs)

    monkeypatch.setattr(totp_router, "check_and_raise", revoke_before_lock)
    response = await client.post(
        f"/api/auth/totp/{action}", json={"code": pyotp.TOTP(secret).now()}
    )
    assert response.status_code == 401
    async with app.state.sessionmaker() as db:
        user = await db.scalar(select(User).where(User.username == username))
        assert user.totp_enabled


async def test_required_totp_only_allows_enrollment(client, app, user_factory):
    username, password = await user_factory()
    app.state.settings.totp_required = True
    app.state.settings_service.invalidate()
    await client.post("/api/auth/login", json=dict(username=username, password=password))
    assert (await client.get("/api/extensions")).status_code == 403
    assert (await client.get("/api/auth/me")).status_code == 200
    await _setup_and_enable_totp(client, username, password)
    assert (await client.get("/api/extensions")).status_code == 200


async def test_mcp_login_requires_totp(client, app, user_factory):
    username, password = await user_factory()
    secret, _ = await _setup_and_enable_totp(client, username, password)
    client_id = await _register_client(client)
    data = await _form(
        client, _ticket(app, client_id=client_id), username=username, password=password
    )
    for code in ("", "invalid"):
        response = await client.post("/mcp-login/callback", data={**data, "code": code})
        assert response.status_code == 401
        assert await _count(app, McpOAuthCode) == 0
    response = await client.post(
        "/mcp-login/callback", data={**data, "code": pyotp.TOTP(secret).now()}
    )
    assert "code=" in _redirect_location(response)


async def test_oauth_code_is_single_use_under_concurrency(app, user_factory):
    username, _ = await user_factory()
    provider, client, code = await _grant(app, username)
    results = await asyncio.gather(
        *[provider.exchange_authorization_code(client, code) for _ in range(2)],
        return_exceptions=True,
    )
    assert sum(isinstance(r, TokenError) for r in results) == 1
    assert await _count(app, McpOAuthToken, McpOAuthToken.kind == "access") == 1


async def test_oauth_code_cannot_survive_password_reset(app, user_factory):
    username, _ = await user_factory()
    provider, client, code = await _grant(app, username)
    async with app.state.sessionmaker() as db:
        user = await db.scalar(select(User).where(User.username == username))
        user.session_epoch += 1
        await db.commit()
    with pytest.raises(TokenError):
        await provider.exchange_authorization_code(client, code)
    assert await _count(app, McpOAuthToken) == 0


@pytest.mark.parametrize("recreate", [False, True])
async def test_oauth_deleted_user_cannot_access_or_refresh(app, user_factory, recreate):
    username, _ = await user_factory()
    provider, client, code = await _grant(app, username)
    tokens = await provider.exchange_authorization_code(client, code)
    refresh = await provider.load_refresh_token(client, tokens.refresh_token)
    async with app.state.sessionmaker() as db:
        user = await db.scalar(select(User).where(User.username == username))
        uid = user.id
        await db.delete(user)
        await db.commit()
        if recreate:
            db.add(
                User(
                    id=uid,
                    username=username,
                    hashed_password=hash_password("DifferentPassword"),
                    display_name=username,
                )
            )
            await db.commit()
    assert await provider.load_access_token(tokens.access_token) is None
    with pytest.raises(TokenError):
        await provider.exchange_refresh_token(client, refresh, [])


async def test_oauth_refresh_preserves_resource_and_cannot_replay(app, user_factory):
    username, _ = await user_factory()
    provider, client, code = await _grant(app, username)
    tokens = await provider.exchange_authorization_code(client, code)
    refresh = await provider.load_refresh_token(client, tokens.refresh_token)
    rotated = await provider.exchange_refresh_token(client, refresh, [])
    access = await provider.load_access_token(rotated.access_token)
    assert access.resource == app.state.settings.mcp_issuer_url
    with pytest.raises(TokenError):
        await provider.exchange_refresh_token(client, refresh, [])


async def test_oauth_wrong_resource_rejected(app, user_factory):
    username, _ = await user_factory()
    provider, client, code = await _grant(app, username)
    state = await provider._current_user_state(username)
    with pytest.raises(ValueError, match="resource"):
        await provider.create_auth_code(
            client_id=client.client_id,
            username=username,
            code_challenge="x",
            redirect_uri=str(client.redirect_uris[0]),
            scopes=[],
            user_state=state[1],
            resource="https://other.example",
        )


def test_media_ticket_binding_replay_and_expiry(monkeypatch):
    tickets = MediaStreamTickets()
    token = tickets.issue("call-1", "7")
    assert not tickets.consume(token, "call-2", "7")
    assert not tickets.consume(token, "call-1", "8")
    assert tickets.consume(token, "call-1", "7")
    assert not tickets.consume(token, "call-1", "7")
    token = tickets.issue("call-1", "7")
    now = time.monotonic()
    monkeypatch.setattr("millicall.media.tickets.time.monotonic", lambda: now + 61)
    assert not tickets.consume(token, "call-1", "7")


async def test_media_handshake_rejects_before_accepting_or_building():
    app = FastAPI()
    app.state.media_tickets = MediaStreamTickets()
    register_media_ws(app)
    endpoint = next(
        r.endpoint for r in app.routes if getattr(r, "path", "") == "/media/audio-fork/{call_uuid}"
    )
    ws = SimpleNamespace(
        app=app, query_params={"agent": "1"}, accept=AsyncMock(), close=AsyncMock()
    )
    await endpoint(ws, "call-1")
    ws.accept.assert_not_awaited()
    ws.close.assert_awaited_once_with(code=1008)


@pytest.mark.parametrize(
    "path", ["system::id", "/tmp/a.wav\n", "/tmp/a.wav aleg\n\napi status", "execute_extension::x"]
)
async def test_playback_cannot_execute_esl_applications(path):
    esl = SimpleNamespace(bgapi=AsyncMock())
    control = EslCallControl(esl, "call-1")
    with pytest.raises(ValueError):
        await control.play_file(path)
    esl.bgapi.assert_not_awaited()


def test_allowlists_reject_trailing_newline():
    assert not is_valid_interface("eth0\n")
    assert not is_valid_hostname("phone\n")
    with pytest.raises(ValueError):
        EslCallControl(None, "call-1\n")


async def test_ssrf_pinned_transport_rejects_unvalidated_host():
    async with _PinnedTransport("public.example", "93.184.216.34") as transport:
        with pytest.raises(ValueError, match="host"):
            await transport.handle_async_request(httpx.Request("GET", "http://127.0.0.1/"))
    assert _is_blocked_ip("100.100.100.200")


def test_continuous_speech_cannot_grow_buffer_without_bound():
    segmenter = VadSegmenter(
        classifier=SimpleNamespace(is_speech=lambda *_: True), max_speech_ms=300
    )
    events = []
    for _ in range(100):
        events.extend(segmenter.push(b"\0" * 480))
        assert len(segmenter._speech_frames) <= 10
    assert [e.kind for e in events] == ["speech_start", "speech_end"] * 10
    assert all(len(e.audio) <= 4800 for e in events)


async def test_cookie_cannot_authenticate_recreated_account(client, app, user_factory):
    username, password = await user_factory()
    await client.post("/api/auth/login", json=dict(username=username, password=password))
    async with app.state.sessionmaker() as db:
        user = await db.scalar(select(User).where(User.username == username))
        uid = user.id
        await db.delete(user)
        await db.commit()
        db.add(
            User(
                id=uid,
                username=username,
                hashed_password=hash_password(password),
                display_name=username,
            )
        )
        await db.commit()
    assert (await client.get("/api/auth/me")).status_code == 401


@pytest.mark.parametrize("slow", [False, True])
async def test_workflow_http_response_is_bounded(monkeypatch, slow):
    from millicall.workflows.context import ChannelContext
    from millicall.workflows.handlers import logic

    class Stream(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            if slow:
                await asyncio.sleep(1)
            yield b"x" * (logic.MAX_API_RESPONSE_BYTES + 1)

        async def aclose(self):
            self.closed = True

    stream = Stream()
    monkeypatch.setattr(logic, "_resolve_and_check_ssrf", lambda _: "93.184.216.34")
    monkeypatch.setattr(
        logic,
        "_PinnedTransport",
        lambda *_: httpx.MockTransport(lambda _: httpx.Response(200, stream=stream)),
    )
    config = SimpleNamespace(
        url="https://public.example",
        body_template="",
        headers={},
        content_type="json",
        method="GET",
        timeout=0.02,
        result_variable="result",
    )
    ctx = ChannelContext(variables={"result": "stale", "result_status": "200"})
    assert await logic.handle_api_call(SimpleNamespace(config=config), ctx) == "error"
    assert ctx.variables == {"result": "", "result_status": ""}
    assert stream.closed


def test_concurrent_startup_does_not_replace_secret_keys(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from millicall.secrets_store import load_or_create_secrets

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: load_or_create_secrets(tmp_path), range(16)))
    assert all(item == results[0] for item in results)
    assert load_or_create_secrets(tmp_path) == results[0]
    assert (tmp_path / "secrets.json").stat().st_mode & 0o777 == 0o600
