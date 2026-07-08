"""CSRF 保護のテスト（Phase 6 Task 3）。

カバレッジ:
  - Cookie 認証済みの POST で X-CSRF-Token なし → 403
  - 正しいトークン付きの POST → 許可
  - GET はトークンなしでも許可
  - 除外パス（/api/auth/login, /api/auth/login/totp, /saml/, /scim/, /mcp）は免除
  - トークン不一致 → 403
  - ログアウト時に csrf Cookie が削除される
  - GET /api/auth/csrf でトークンを取得できる
"""

import pyotp

from millicall.auth.security import hash_password
from millicall.config import Settings
from millicall.main import create_app
from millicall.models import User
from tests.conftest import CsrfAwareClient

# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------


async def _make_app(tmp_path):
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        fs_config_dir=tmp_path / "fs",
        cookie_secure=False,
        esl_timeout_seconds=1.0,
    )
    return create_app(settings)


async def _create_user(app, username="csrfuser", password="Passw0rd1"):
    sm = app.state.sessionmaker
    async with sm() as s:
        s.add(
            User(
                username=username,
                hashed_password=hash_password(password),
                display_name=username,
                role="admin",
                origin="local",
            )
        )
        await s.commit()
    return username, password


# ---------------------------------------------------------------------------
# 基本 CSRF チェック
# ---------------------------------------------------------------------------


async def test_post_without_csrf_token_rejected(tmp_path):
    """セッション Cookie あり + X-CSRF-Token なし → 403。"""
    app = await _make_app(tmp_path)
    async with app.router.lifespan_context(app):
        await _create_user(app)
        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            # ログイン（/api/auth/login は除外パスなので CSRF 不要）
            resp = await c.post(
                "/api/auth/login", json={"username": "csrfuser", "password": "Passw0rd1"}
            )
            assert resp.status_code == 200
            # セッション Cookie はセットされているが X-CSRF-Token ヘッダーを付けない
            assert c.cookies.get("millicall_session")

            # 認証が必要な POST（CSRF トークンなし）
            resp = await c.post("/api/auth/logout")
            assert resp.status_code == 403
            assert resp.json()["detail"] == "CSRF token missing or invalid"


async def test_post_with_correct_csrf_token_allowed(tmp_path):
    """セッション Cookie あり + 正しい X-CSRF-Token → 許可。"""
    app = await _make_app(tmp_path)
    async with app.router.lifespan_context(app):
        await _create_user(app)
        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post(
                "/api/auth/login", json={"username": "csrfuser", "password": "Passw0rd1"}
            )
            assert resp.status_code == 200
            csrf_token = c.cookies.get("millicall_csrf")
            assert csrf_token, "ログイン成功時に CSRF Cookie がセットされていない"

            # 正しいトークンを付けて POST → 成功
            resp = await c.post("/api/auth/logout", headers={"X-CSRF-Token": csrf_token})
            assert resp.status_code == 200


async def test_get_without_csrf_token_allowed(tmp_path):
    """GET リクエストは CSRF チェックを受けない。"""
    app = await _make_app(tmp_path)
    async with app.router.lifespan_context(app):
        await _create_user(app)
        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            await c.post("/api/auth/login", json={"username": "csrfuser", "password": "Passw0rd1"})
            # GET /api/auth/me はトークンなしでも OK
            resp = await c.get("/api/auth/me")
            assert resp.status_code == 200


async def test_token_mismatch_rejected(tmp_path):
    """X-CSRF-Token が Cookie と不一致 → 403。"""
    app = await _make_app(tmp_path)
    async with app.router.lifespan_context(app):
        await _create_user(app)
        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post(
                "/api/auth/login", json={"username": "csrfuser", "password": "Passw0rd1"}
            )
            assert resp.status_code == 200

            # 別の（ランダムな）トークンを付ける
            resp = await c.post("/api/auth/logout", headers={"X-CSRF-Token": "wrong-token-xyz"})
            assert resp.status_code == 403


async def test_request_without_session_cookie_exempt(tmp_path):
    """セッション Cookie がない（Bearer 認証等）リクエストは CSRF 免除。"""
    app = await _make_app(tmp_path)
    async with app.router.lifespan_context(app):
        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            # 未認証で POST /api/auth/login → CSRF 免除パスなので 401 または 422 のみ（403 ではない）
            resp = await c.post("/api/auth/login", json={"username": "nobody", "password": "nope"})
            assert resp.status_code != 403


# ---------------------------------------------------------------------------
# 除外パスのテスト
# ---------------------------------------------------------------------------


async def test_login_path_exempt_from_csrf(tmp_path):
    """POST /api/auth/login は CSRF 免除（pre-auth）。"""
    app = await _make_app(tmp_path)
    async with app.router.lifespan_context(app):
        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            # セッション Cookie があっても /api/auth/login は免除
            # まず何らかのセッション Cookie を強制セット（CSRF チェックが走るか確認）
            c.cookies.set("millicall_session", "fake-session-value")
            resp = await c.post("/api/auth/login", json={"username": "ghost", "password": "nope"})
            # CSRF エラーではなく認証エラー（401）を返す
            assert resp.status_code != 403


async def test_login_totp_path_exempt_from_csrf(tmp_path):
    """POST /api/auth/login/totp は CSRF 免除（pre-auth）。"""
    app = await _make_app(tmp_path)
    async with app.router.lifespan_context(app):
        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            c.cookies.set("millicall_session", "fake-session-value")
            resp = await c.post(
                "/api/auth/login/totp",
                json={"ticket": "invalid.ticket", "code": "123456"},
            )
            # CSRF エラー（403）ではなく認証エラー（401）
            assert resp.status_code != 403


async def test_mcp_path_exempt_from_csrf(tmp_path):
    """/mcp パスは CSRF 免除（OAuth Bearer 認証）。"""
    app = await _make_app(tmp_path)
    async with app.router.lifespan_context(app):
        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            c.cookies.set("millicall_session", "fake-session-value")
            # /mcp への POST は CSRF 免除（OAuth Bearer）
            # 実際の認証エラー等は返るが 403 CSRF エラーではない
            resp = await c.post("/mcp", content=b"{}")
            assert resp.status_code != 403


# ---------------------------------------------------------------------------
# CSRF Cookie の発行・削除確認
# ---------------------------------------------------------------------------


async def test_csrf_cookie_set_on_login(tmp_path):
    """ログイン成功時に non-HttpOnly な CSRF Cookie がセットされる。"""
    app = await _make_app(tmp_path)
    async with app.router.lifespan_context(app):
        await _create_user(app)
        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post(
                "/api/auth/login", json={"username": "csrfuser", "password": "Passw0rd1"}
            )
            assert resp.status_code == 200
            # CSRF Cookie がセットされている
            assert "millicall_csrf" in resp.cookies
            csrf_token = resp.cookies.get("millicall_csrf")
            assert csrf_token and len(csrf_token) > 10


async def test_csrf_cookie_cleared_on_logout(tmp_path):
    """ログアウト時に CSRF Cookie が削除される。"""
    app = await _make_app(tmp_path)
    async with app.router.lifespan_context(app):
        await _create_user(app)
        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            await c.post("/api/auth/login", json={"username": "csrfuser", "password": "Passw0rd1"})
            csrf_token = c.cookies.get("millicall_csrf")
            assert csrf_token

            resp = await c.post("/api/auth/logout", headers={"X-CSRF-Token": csrf_token})
            assert resp.status_code == 200
            # httpx では delete_cookie は max-age=0 で上書き; cookie jar から消えているか確認
            assert c.cookies.get("millicall_csrf") is None or c.cookies.get("millicall_csrf") == ""


async def test_get_csrf_endpoint(tmp_path):
    """GET /api/auth/csrf でトークンを取得できる。"""
    app = await _make_app(tmp_path)
    async with app.router.lifespan_context(app):
        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/auth/csrf")
            assert resp.status_code == 200
            data = resp.json()
            assert "csrf" in data
            assert data["csrf"] and len(data["csrf"]) > 10


async def test_csrf_cookie_set_on_totp_login(tmp_path):
    """TOTP 2 段階ログイン成功時にも CSRF Cookie がセットされる。"""

    app = await _make_app(tmp_path)
    async with app.router.lifespan_context(app):
        await _create_user(app, username="csrf_totp_user", password="Passw0rd1!")
        from httpx import ASGITransport

        transport = ASGITransport(app=app)
        # TOTP セットアップ
        async with CsrfAwareClient(transport=transport, base_url="http://test") as c:
            await c.post(
                "/api/auth/login",
                json={"username": "csrf_totp_user", "password": "Passw0rd1!"},
            )
            setup = await c.post("/api/auth/totp/setup")
            assert setup.status_code == 200
            secret = setup.json()["secret"]
            code = pyotp.TOTP(secret).now()
            await c.post("/api/auth/totp/verify", json={"code": code})

        # 新しいクライアントで 2 段階ログイン
        async with CsrfAwareClient(transport=transport, base_url="http://test") as c:
            login_resp = await c.post(
                "/api/auth/login",
                json={"username": "csrf_totp_user", "password": "Passw0rd1!"},
            )
            assert login_resp.status_code == 200
            ticket = login_resp.json()["ticket"]

            totp_resp = await c.post(
                "/api/auth/login/totp",
                json={"ticket": ticket, "code": pyotp.TOTP(secret).now()},
            )
            assert totp_resp.status_code == 200
            # TOTP ログイン成功後も CSRF Cookie がセットされる
            assert "millicall_csrf" in totp_resp.cookies


# ---------------------------------------------------------------------------
# 除外パスの境界一致（レビュー M-1/M-2 回帰）
# ---------------------------------------------------------------------------


def test_csrf_exempt_boundary_matching():
    """startswith の過剰マッチを防ぎ、境界一致のみ除外する。"""
    from millicall.auth.csrf import _is_exempt

    # 正当な除外
    assert _is_exempt("/api/auth/login") is True
    assert _is_exempt("/api/auth/login/totp") is True
    assert _is_exempt("/mcp") is True
    assert _is_exempt("/mcp/messages") is True
    assert _is_exempt("/scim/v2/Users") is True
    assert _is_exempt("/saml/acs") is True
    # 過剰マッチしてはならないもの
    assert _is_exempt("/api/auth/login-history") is False
    assert _is_exempt("/mcp-login/callback") is False
    assert _is_exempt("/api/auth/logout") is False
    assert _is_exempt("/api/network") is False
