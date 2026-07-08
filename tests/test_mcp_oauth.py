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
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )
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
    await _make_user(
        mcp_app, username="disabled1", password="DisPass1", role="admin", enabled=False
    )
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


# --------------------------------------------------------------------------
# 監査 C1: MCP Bearer の失効契約（enabled / session_epoch 照合）
# --------------------------------------------------------------------------
async def test_access_token_revoked_when_user_disabled(mcp_app):
    """発行後にユーザーを無効化すると load_access_token が拒否する。"""
    from mcp.server.auth.provider import AccessToken
    from sqlalchemy import select

    from millicall.models import User

    await _make_user(mcp_app, username="c1disable", password="Pass1234x", role="admin")
    provider = mcp_app.state.mcp_oauth_provider

    # トークンを手動登録し、発行時 epoch を記録する（exchange 経路の内部状態を再現）。
    provider._access_tokens["tok-c1a"] = AccessToken(
        token="tok-c1a", client_id="c", scopes=[], expires_at=None, subject="c1disable"
    )
    await provider._record_token_epoch("c1disable", "tok-c1a", "ref-c1a")

    # 有効なうちは通る
    assert await provider.load_access_token("tok-c1a") is not None

    # 無効化 → 拒否
    async with mcp_app.state.sessionmaker() as s:
        u = await s.scalar(select(User).where(User.username == "c1disable"))
        u.enabled = False
        await s.commit()
    assert await provider.load_access_token("tok-c1a") is None


async def test_access_token_revoked_when_epoch_bumped(mcp_app):
    """session_epoch を bump すると発行済みトークンが失効する（パスワード変更/logout-all 等）。"""
    from mcp.server.auth.provider import AccessToken
    from sqlalchemy import select

    from millicall.models import User

    await _make_user(mcp_app, username="c1epoch", password="Pass1234x", role="admin")
    provider = mcp_app.state.mcp_oauth_provider
    provider._access_tokens["tok-c1e"] = AccessToken(
        token="tok-c1e", client_id="c", scopes=[], expires_at=None, subject="c1epoch"
    )
    await provider._record_token_epoch("c1epoch", "tok-c1e", "ref-c1e")

    assert await provider.load_access_token("tok-c1e") is not None

    async with mcp_app.state.sessionmaker() as s:
        u = await s.scalar(select(User).where(User.username == "c1epoch"))
        u.session_epoch = (u.session_epoch or 0) + 1
        await s.commit()
    assert await provider.load_access_token("tok-c1e") is None


# --------------------------------------------------------------------------
# H2: /mcp-login/callback レート制限（ブルートフォース対策）
# --------------------------------------------------------------------------


def _mcp_settings_with_throttle(tmp_path, max_attempts: int = 3, lockout_seconds: int = 60):
    """レート制限を小さい値に設定した MCP 用 Settings を生成する。"""
    return Settings(
        data_dir=tmp_path,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        fs_config_dir=tmp_path / "fs",
        cookie_secure=False,
        esl_timeout_seconds=1.0,
        mcp_issuer_url="http://localhost",
        mcp_allowed_hosts=["localhost", "127.0.0.1"],
        static_dir=tmp_path / "no-static",
        login_max_attempts=max_attempts,
        login_lockout_seconds=lockout_seconds,
    )


async def test_mcp_callback_throttle_username_lockout(tmp_path):
    """同一ユーザー名で連続失敗すると /mcp-login/callback が 429 を返す（H2）。"""
    app = create_app(_mcp_settings_with_throttle(tmp_path, max_attempts=3))
    async with app.router.lifespan_context(app):
        await _make_user(app, username="th1", password="Passw0rd1", role="admin")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://localhost") as c:
            ticket = _ticket(app, client_id="cli")
            # 3 回失敗
            for _ in range(3):
                r = await c.post(
                    "/mcp-login/callback",
                    data={"ticket": ticket, "username": "th1", "password": "WrongPass"},
                )
                assert r.status_code == 401
            # 4 回目は 429
            r = await c.post(
                "/mcp-login/callback",
                data={"ticket": ticket, "username": "th1", "password": "WrongPass"},
            )
            assert r.status_code == 429


async def test_mcp_callback_throttle_retry_after_header(tmp_path):
    """429 レスポンスに Retry-After ヘッダーが含まれる（H2）。"""
    app = create_app(_mcp_settings_with_throttle(tmp_path, max_attempts=2, lockout_seconds=120))
    async with app.router.lifespan_context(app):
        await _make_user(app, username="th2", password="Passw0rd1", role="admin")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://localhost") as c:
            ticket = _ticket(app, client_id="cli")
            for _ in range(2):
                await c.post(
                    "/mcp-login/callback",
                    data={"ticket": ticket, "username": "th2", "password": "WrongPass"},
                )
            r = await c.post(
                "/mcp-login/callback",
                data={"ticket": ticket, "username": "th2", "password": "WrongPass"},
            )
            assert r.status_code == 429
            assert r.headers.get("Retry-After") == "120"


async def test_mcp_callback_success_clears_counter(tmp_path):
    """ログイン成功でカウンタがリセットされ、再び試行できる（H2）。"""
    app = create_app(_mcp_settings_with_throttle(tmp_path, max_attempts=3))
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        await _make_user(app, username="th3", password="Passw0rd1", role="admin")
        async with AsyncClient(transport=transport, base_url="http://localhost") as c:
            # DCR でクライアント登録
            client_id = await _register_client(c)
            verifier, challenge = _pkce()

            # 2 回失敗（上限未満）
            ticket = _ticket(app, client_id="cli")
            for _ in range(2):
                r = await c.post(
                    "/mcp-login/callback",
                    data={"ticket": ticket, "username": "th3", "password": "WrongPass"},
                )
                assert r.status_code == 401

            # 成功でリセット（登録済みクライアント経由）
            ticket2 = _ticket(app, client_id=client_id, code_challenge=challenge, state="ok")
            r = await c.post(
                "/mcp-login/callback",
                data={"ticket": ticket2, "username": "th3", "password": "Passw0rd1"},
            )
            assert r.status_code == 302

            # 再び 2 回失敗しても 429 にならない（カウンタがリセット済み）
            ticket3 = _ticket(app, client_id="cli")
            for _ in range(2):
                r = await c.post(
                    "/mcp-login/callback",
                    data={"ticket": ticket3, "username": "th3", "password": "WrongPass"},
                )
                assert r.status_code == 401


async def test_mcp_callback_disabled_user_records_failure(tmp_path):
    """無効化ユーザー認証試行も失敗として記録され、ロックアウトが発動する（H2）。"""
    from sqlalchemy import func
    from sqlalchemy import select as sa_select

    from millicall.models import LoginAttempt

    app = create_app(_mcp_settings_with_throttle(tmp_path, max_attempts=3))
    async with app.router.lifespan_context(app):
        await _make_user(app, username="th4", password="Passw0rd1", role="admin", enabled=False)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://localhost") as c:
            ticket = _ticket(app, client_id="cli")
            for _ in range(3):
                await c.post(
                    "/mcp-login/callback",
                    data={"ticket": ticket, "username": "th4", "password": "Passw0rd1"},
                )
            # 4 回目は 429（失敗記録が蓄積されロックアウト）
            r = await c.post(
                "/mcp-login/callback",
                data={"ticket": ticket, "username": "th4", "password": "Passw0rd1"},
            )
            assert r.status_code == 429

        sm = app.state.sessionmaker
        async with sm() as s:
            count = await s.scalar(
                sa_select(func.count())
                .select_from(LoginAttempt)
                .where(LoginAttempt.username == "th4")
            )
        assert count and count >= 3
