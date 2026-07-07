"""Phase 4a Task 1: MCP OAuth 2.1 + /mcp マウント + SPA 除外のテスト。

- 認可サーバー / 保護リソースメタデータの形状
- /mcp-login (GET HTML) と callback の認証/ロールゲート
- DCR(RFC7591) → /mcp-login/callback → PKCE token → Bearer で /mcp 200 / 無トークン 401
- SPA catch-all が /mcp・/.well-known を食わない
- TransportSecurity allowed_hosts の適用
"""

import base64
import hashlib
import secrets
import urllib.parse

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from millicall.config import Settings
from millicall.main import create_app

MCP_INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1.0"},
    },
}
MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


def _mcp_settings(tmp_path, static_dir=None):
    return Settings(
        data_dir=tmp_path,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        fs_config_dir=tmp_path / "fs",
        cookie_secure=False,
        esl_timeout_seconds=1.0,
        mcp_issuer_url="http://localhost",
        mcp_allowed_hosts=["localhost", "127.0.0.1"],
        static_dir=static_dir if static_dir is not None else tmp_path / "no-static",
    )


@pytest_asyncio.fixture
async def mcp_app(tmp_path):
    app = create_app(_mcp_settings(tmp_path))
    async with app.router.lifespan_context(app):
        yield app


@pytest_asyncio.fixture
async def mcp_client(mcp_app):
    transport = ASGITransport(app=mcp_app)
    async with AsyncClient(transport=transport, base_url="http://localhost") as c:
        yield c


async def _make_user(app, username="mcpadmin", password="Passw0rd1", role="admin", enabled=True):
    from millicall.auth.security import hash_password
    from millicall.models import User

    async with app.state.sessionmaker() as session:
        session.add(
            User(
                username=username,
                hashed_password=hash_password(password),
                display_name=username,
                role=role,
                origin="local",
                enabled=enabled,
            )
        )
        await session.commit()
    return username, password


def _pkce():
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def _ticket(
    app,
    *,
    client_id="cli",
    redirect_uri="https://claude.ai/cb",
    code_challenge="ch",
    state="",
    scopes=None,
    resource="",
    explicit=True,
):
    """authorize() が発行する署名済みログインチケットを模して生成する。"""
    return app.state.mcp_oauth_provider.sign_login_ticket(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": code_challenge,
            "state": state,
            "scopes": scopes if scopes is not None else [],
            "resource": resource,
            "explicit": explicit,
        }
    )


async def _register_client(mcp_client, redirect_uris=None):
    reg = await mcp_client.post(
        "/register",
        json={
            "redirect_uris": redirect_uris or ["https://claude.ai/cb"],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
        },
    )
    assert reg.status_code == 201
    return reg.json()["client_id"]


# --------------------------------------------------------------------------
# メタデータ
# --------------------------------------------------------------------------
async def test_authorization_server_metadata(mcp_client):
    r = await mcp_client.get("/.well-known/oauth-authorization-server")
    assert r.status_code == 200
    data = r.json()
    assert data["issuer"].rstrip("/") == "http://localhost"
    assert data["authorization_endpoint"] == "http://localhost/authorize"
    assert data["token_endpoint"] == "http://localhost/token"
    assert data["registration_endpoint"] == "http://localhost/register"
    assert "S256" in data["code_challenge_methods_supported"]


async def test_protected_resource_metadata(mcp_client):
    r = await mcp_client.get("/.well-known/oauth-protected-resource")
    assert r.status_code == 200
    data = r.json()
    assert "authorization_servers" in data
    assert data["resource"].rstrip("/") == "http://localhost"


# --------------------------------------------------------------------------
# ログインページ
# --------------------------------------------------------------------------
async def test_mcp_login_page_renders_form(mcp_app, mcp_client):
    ticket = _ticket(mcp_app, client_id="abc")
    r = await mcp_client.get("/mcp-login", params={"ticket": ticket})
    assert r.status_code == 200
    assert 'action="/mcp-login/callback"' in r.text
    # 認可パラメータはチケットにのみ封入され、生の client_id 等はフォームに出さない。
    assert 'name="ticket"' in r.text
    assert 'name="client_id"' not in r.text
    assert 'name="explicit"' not in r.text


# --------------------------------------------------------------------------
# 認証 / ロールゲート
# --------------------------------------------------------------------------
async def test_login_callback_rejects_bad_password(mcp_app, mcp_client):
    await _make_user(mcp_app, username="u1", password="RightPass1", role="admin")
    r = await mcp_client.post(
        "/mcp-login/callback",
        data={"ticket": _ticket(mcp_app), "username": "u1", "password": "WrongPass"},
    )
    assert r.status_code == 401


async def test_login_callback_rejects_disallowed_role(mcp_app, mcp_client):
    await _make_user(mcp_app, username="guest1", password="GuestPass1", role="guest")
    r = await mcp_client.post(
        "/mcp-login/callback",
        data={"ticket": _ticket(mcp_app), "username": "guest1", "password": "GuestPass1"},
    )
    assert r.status_code == 403


async def test_login_callback_rejects_disabled_user(mcp_app, mcp_client):
    """無効化されたユーザーは MCP OAuth ログインを 403 で拒否される（全体レビュー minor #1）。"""
    await _make_user(mcp_app, username="disabled1", password="DisPass1", role="admin", enabled=False)
    r = await mcp_client.post(
        "/mcp-login/callback",
        data={"ticket": _ticket(mcp_app), "username": "disabled1", "password": "DisPass1"},
    )
    assert r.status_code == 403


# --------------------------------------------------------------------------
# /mcp 認証必須
# --------------------------------------------------------------------------
async def test_mcp_requires_bearer_token(mcp_client):
    r = await mcp_client.post("/mcp", json=MCP_INIT, headers=MCP_HEADERS)
    assert r.status_code == 401
    assert "resource_metadata" in r.headers.get("www-authenticate", "")


# --------------------------------------------------------------------------
# フルフロー: DCR → login → PKCE token → /mcp
# --------------------------------------------------------------------------
async def test_full_oauth_flow(mcp_app, mcp_client):
    await _make_user(mcp_app, username="flow", password="FlowPass1", role="admin")

    # DCR
    client_id = await _register_client(mcp_client)
    verifier, challenge = _pkce()

    # ログイン → 認可コード（302 redirect）。認可パラメータは署名チケット経由。
    ticket = _ticket(mcp_app, client_id=client_id, code_challenge=challenge, state="s1")
    cb = await mcp_client.post(
        "/mcp-login/callback",
        data={"ticket": ticket, "username": "flow", "password": "FlowPass1"},
    )
    assert cb.status_code == 302
    location = cb.headers["location"]
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)
    assert qs["state"] == ["s1"]
    code = qs["code"][0]

    # トークン交換（PKCE）
    tok = await mcp_client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "client_id": client_id,
            "redirect_uri": "https://claude.ai/cb",
        },
    )
    assert tok.status_code == 200
    access = tok.json()["access_token"]
    assert access

    # Bearer で /mcp initialize → 200
    ok = await mcp_client.post(
        "/mcp",
        json=MCP_INIT,
        headers={**MCP_HEADERS, "Authorization": f"Bearer {access}"},
    )
    assert ok.status_code == 200

    # 誤トークンは 401
    bad = await mcp_client.post(
        "/mcp",
        json=MCP_INIT,
        headers={**MCP_HEADERS, "Authorization": "Bearer nope"},
    )
    assert bad.status_code == 401


async def test_wrong_pkce_verifier_rejected(mcp_app, mcp_client):
    await _make_user(mcp_app, username="pk", password="PkPass1", role="admin")
    client_id = await _register_client(mcp_client)
    _, challenge = _pkce()
    cb = await mcp_client.post(
        "/mcp-login/callback",
        data={
            "ticket": _ticket(mcp_app, client_id=client_id, code_challenge=challenge),
            "username": "pk",
            "password": "PkPass1",
        },
    )
    code = urllib.parse.parse_qs(urllib.parse.urlparse(cb.headers["location"]).query)["code"][0]
    tok = await mcp_client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": secrets.token_urlsafe(64),  # 誤った verifier
            "client_id": client_id,
            "redirect_uri": "https://claude.ai/cb",
        },
    )
    assert tok.status_code == 400


# --------------------------------------------------------------------------
# ツール登録 / DI 契約
# --------------------------------------------------------------------------
async def test_ping_tool_registered(mcp_app):
    tools = await mcp_app.state.mcp.list_tools()
    names = {t.name for t in tools}
    assert "ping" in names


async def test_app_state_di_bridge(mcp_app):
    from millicall.mcp_server import get_app_state

    state = get_app_state(mcp_app.state.mcp)
    # lifespan で埋まる依存が DI ブリッジ越しに見えること（後続タスクのツールが使う）。
    assert state.sessionmaker is mcp_app.state.sessionmaker


# --------------------------------------------------------------------------
# TransportSecurity allowed_hosts
# --------------------------------------------------------------------------
async def test_allowed_hosts_wired(mcp_app):
    hosts = mcp_app.state.mcp.settings.transport_security.allowed_hosts
    assert "localhost" in hosts
    assert "127.0.0.1" in hosts


async def test_disallowed_host_rejected_on_mcp(mcp_app, mcp_client):
    # 有効トークンを得てから、許可外 Host で /mcp を叩くと拒否される（DNS リバインド対策）。
    await _make_user(mcp_app, username="host", password="HostPass1", role="admin")
    client_id = await _register_client(mcp_client)
    verifier, challenge = _pkce()
    cb = await mcp_client.post(
        "/mcp-login/callback",
        data={
            "ticket": _ticket(mcp_app, client_id=client_id, code_challenge=challenge),
            "username": "host",
            "password": "HostPass1",
        },
    )
    code = urllib.parse.parse_qs(urllib.parse.urlparse(cb.headers["location"]).query)["code"][0]
    tok = await mcp_client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "client_id": client_id,
            "redirect_uri": "https://claude.ai/cb",
        },
    )
    access = tok.json()["access_token"]
    r = await mcp_client.post(
        "/mcp",
        json=MCP_INIT,
        headers={**MCP_HEADERS, "Authorization": f"Bearer {access}", "Host": "evil.example"},
    )
    assert r.status_code != 200


# --------------------------------------------------------------------------
# mcp_enabled=False
# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# セキュリティ: 署名チケット / redirect_uri バインディング（HIGH 指摘の回帰）
# --------------------------------------------------------------------------
async def test_login_callback_rejects_tampered_ticket(mcp_app, mcp_client):
    await _make_user(mcp_app, username="t1", password="TamperPass1", role="admin")
    ticket = _ticket(mcp_app, client_id="cli")
    r = await mcp_client.post(
        "/mcp-login/callback",
        data={"ticket": ticket + "x", "username": "t1", "password": "TamperPass1"},
    )
    assert r.status_code == 400


async def test_login_callback_rejects_unregistered_redirect_uri(mcp_app, mcp_client):
    # 正規ログインに成功しても、redirect_uri が client 登録値に無ければコードを出さない
    # （open redirect / 認可コード漏洩対策）。
    await _make_user(mcp_app, username="rr", password="RedirPass1", role="admin")
    client_id = await _register_client(mcp_client, redirect_uris=["https://claude.ai/cb"])
    _, challenge = _pkce()
    ticket = _ticket(
        mcp_app,
        client_id=client_id,
        redirect_uri="https://evil.example/steal",
        code_challenge=challenge,
    )
    r = await mcp_client.post(
        "/mcp-login/callback",
        data={"ticket": ticket, "username": "rr", "password": "RedirPass1"},
    )
    assert r.status_code == 400


async def test_login_callback_rejects_unknown_client(mcp_app, mcp_client):
    await _make_user(mcp_app, username="uc", password="UnknownPass1", role="admin")
    _, challenge = _pkce()
    # DCR していない client_id をチケットに封じても、コード発行時に fail-closed。
    ticket = _ticket(mcp_app, client_id="never-registered", code_challenge=challenge)
    r = await mcp_client.post(
        "/mcp-login/callback",
        data={"ticket": ticket, "username": "uc", "password": "UnknownPass1"},
    )
    assert r.status_code == 400


async def test_mcp_disabled(tmp_path):
    settings = _mcp_settings(tmp_path)
    settings.mcp_enabled = False
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://localhost") as c:
            r = await c.get("/.well-known/oauth-authorization-server")
            assert r.status_code == 404
            assert not hasattr(app.state, "mcp_session_manager")
