"""issue #88: MCP ログイン画面のブラウザフローと CSRF / CSP の整合。

実ブラウザの代わりに、JS を実行しない通常の HTML form 送信を httpx で再現する:
  - X-CSRF-Token ヘッダーは付けない（CsrfAwareClient ではなく素の AsyncClient を使う）
  - GET /mcp-login が返す Cookie と hidden フィールドだけで POST する
  - 管理画面ログイン済み（セッション Cookie あり）／未ログインの両方を検証する

あわせて、全応答がインライン script を含まず、CSP（script-src 'self'）の下で
JavaScript なしに本文だけでエラーと再試行導線を表示することを確認する。
"""

import html
import re
import urllib.parse

import pyotp
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from millicall.auth.csrf import MCP_LOGIN_CSRF_COOKIE
from millicall.main import create_app
from millicall.models import LoginAttempt
from tests.conftest import CsrfAwareClient
from tests.test_mcp_oauth import (
    _form,
    _make_user,
    _mcp_settings,
    _mcp_settings_with_throttle,
    _pkce,
    _redirect_location,
    _register_client,
    _ticket,
)
from tests.test_totp import _setup_and_enable_totp

_CSRF_FIELD_RE = re.compile(r'name="csrf_token" value="([^"]*)"')
_TICKET_FIELD_RE = re.compile(r'name="ticket" value="([^"]*)"')


@pytest_asyncio.fixture
async def mcp_app(tmp_path):
    app = create_app(_mcp_settings(tmp_path))
    async with app.router.lifespan_context(app):
        yield app


@pytest_asyncio.fixture
async def browser(mcp_app):
    """JS を実行しないブラウザ相当のクライアント（CSRF ヘッダーを自動付与しない）。"""
    transport = ASGITransport(app=mcp_app)
    async with AsyncClient(transport=transport, base_url="http://localhost") as c:
        yield c


async def _admin_session(client, username, password):
    """管理画面にログインし、セッション Cookie と millicall_csrf Cookie を得る。"""
    r = await client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200
    assert client.cookies.get("millicall_session")
    assert client.cookies.get("millicall_csrf")


def _assert_csp_safe_html(r):
    """JS なしで完結する HTML であり、CSP がインライン script を許していないこと。"""
    assert r.headers["content-type"].startswith("text/html")
    assert "<script" not in r.text.lower()
    assert not re.search(r"<[^>]+\son[a-z]+\s*=", r.text, re.IGNORECASE)  # イベント属性なし
    directives = {
        d.strip().split(" ", 1)[0]: d.strip().split(" ", 1)[1:]
        for d in r.headers["content-security-policy"].split(";")
        if d.strip()
    }
    assert directives["script-src"] == ["'self'"]
    # ページはインライン <style> を使う。style-src がそれを許可していること（JS ではない）。
    assert "'unsafe-inline'" in directives["style-src"][0]


def _login_csrf_set_cookie(r) -> str | None:
    for value in r.headers.get_list("set-cookie"):
        if value.startswith(f"{MCP_LOGIN_CSRF_COOKIE}="):
            return value
    return None


def _no_codes(app):
    return not app.state.mcp_oauth_provider._auth_codes


async def _failure_count(app, username):
    async with app.state.sessionmaker() as s:
        return await s.scalar(
            select(func.count()).select_from(LoginAttempt).where(LoginAttempt.username == username)
        )


# --------------------------------------------------------------------------
# GET /mcp-login
# --------------------------------------------------------------------------
async def test_login_page_issues_bound_form_token(mcp_app, browser):
    ticket = _ticket(mcp_app)
    r = await browser.get("/mcp-login", params={"ticket": ticket})
    assert r.status_code == 200
    _assert_csp_safe_html(r)

    set_cookie = _login_csrf_set_cookie(r)
    assert set_cookie is not None
    lowered = set_cookie.lower()
    assert "httponly" in lowered
    assert "samesite=strict" in lowered
    assert "path=/mcp-login" in lowered

    token = _CSRF_FIELD_RE.search(r.text).group(1)
    assert token == browser.cookies.get(MCP_LOGIN_CSRF_COOKIE)
    assert html.unescape(_TICKET_FIELD_RE.search(r.text).group(1)) == ticket


async def test_login_page_reuses_existing_cookie(mcp_app, browser):
    """同一ブラウザで 2 回開いても先に開いたフォームのトークンが無効化されない。"""
    first = await _form(browser, _ticket(mcp_app))
    second = await _form(browser, _ticket(mcp_app))
    assert first["csrf_token"] == second["csrf_token"]


async def test_login_page_rejects_unsigned_ticket(mcp_app, browser):
    for ticket in ("not-a-ticket", _ticket(mcp_app) + "x"):
        r = await browser.get("/mcp-login", params={"ticket": ticket})
        assert r.status_code == 400
        _assert_csp_safe_html(r)
        assert "<form" not in r.text
        assert "MCP クライアントから接続をやり直してください" in r.text
        assert _login_csrf_set_cookie(r) is None


# --------------------------------------------------------------------------
# 正常認証（セッション Cookie なし / あり）
# --------------------------------------------------------------------------
async def _assert_successful_login(app, client, username, password):
    # DCR は MCP クライアントがブラウザ外から行うため、ブラウザの Cookie とは切り離す。
    client_id = await _register_client_via_app(app)
    _, challenge = _pkce()
    ticket = _ticket(app, client_id=client_id, code_challenge=challenge, state="st<&>")
    data = await _form(client, ticket, username=username, password=password)
    r = await client.post("/mcp-login/callback", data=data)
    # 別オリジンへの 302 は CSP form-action 'self' でブラウザに遮断されるため、200 の HTML から
    # meta refresh（JS 不要）とリンクで遷移させる。CSP 自体は緩めていないこと。
    assert "location" not in r.headers
    _assert_csp_safe_html(r)
    assert "form-action 'self'" in r.headers["content-security-policy"]
    assert "claude.ai" not in r.headers["content-security-policy"]
    location = urllib.parse.urlparse(_redirect_location(r))
    assert f'<a class="retry" href="{html.escape(location.geturl())}">' in r.text
    assert f"{location.scheme}://{location.netloc}{location.path}" == "https://claude.ai/cb"
    qs = urllib.parse.parse_qs(location.query)
    assert qs["state"] == ["st<&>"]
    assert qs["code"][0] in app.state.mcp_oauth_provider._auth_codes
    # 認可完了でフォーム用 CSRF Cookie は破棄される。
    assert _login_csrf_set_cookie(r) is not None
    assert client.cookies.get(MCP_LOGIN_CSRF_COOKIE) is None


async def test_success_without_session_cookie(mcp_app, browser):
    await _make_user(mcp_app, username="nos", password="NoSessPass1")
    assert not browser.cookies.get("millicall_session")
    await _assert_successful_login(mcp_app, browser, "nos", "NoSessPass1")


async def test_success_with_admin_session_cookie(mcp_app, browser):
    """管理画面ログイン済みのブラウザでも、ヘッダーなしの form 送信が 403 にならない（#88）。"""
    await _make_user(mcp_app, username="withs", password="WithSessPass1")
    await _admin_session(browser, "withs", "WithSessPass1")
    await _assert_successful_login(mcp_app, browser, "withs", "WithSessPass1")


# --------------------------------------------------------------------------
# CSRF: クロスサイト POST / トークン欠落・不一致 / ログイン CSRF
# --------------------------------------------------------------------------
async def test_cross_site_post_with_session_cookie_rejected(mcp_app, browser):
    """セッション Cookie はあるが専用 Cookie が無い（クロスサイト）POST はミドルウェアで 403。"""
    await _make_user(mcp_app, username="xs1", password="CrossPass1")
    await _admin_session(browser, "xs1", "CrossPass1")
    csrf = browser.cookies.get("millicall_csrf")
    r = await browser.post(
        "/mcp-login/callback",
        # 攻撃者は millicall_csrf の値を知らないが、仮に知っていても代替にならないこと。
        data={
            "ticket": _ticket(mcp_app),
            "csrf_token": csrf,
            "username": "xs1",
            "password": "CrossPass1",
        },
    )
    assert r.status_code == 403
    assert _no_codes(mcp_app)
    assert await _failure_count(mcp_app, "xs1") == 0


async def test_cross_site_post_with_session_cookie_and_header_only_rejected(mcp_app, browser):
    """X-CSRF-Token ヘッダーが正しくても、フォームトークンの検証は省略されない。"""
    await _make_user(mcp_app, username="xs2", password="CrossPass2")
    await _admin_session(browser, "xs2", "CrossPass2")
    await _form(browser, _ticket(mcp_app))  # 専用 Cookie は持っている
    r = await browser.post(
        "/mcp-login/callback",
        data={"ticket": _ticket(mcp_app), "username": "xs2", "password": "CrossPass2"},
        headers={"X-CSRF-Token": browser.cookies.get("millicall_csrf")},
    )
    assert r.status_code == 403
    assert _no_codes(mcp_app)


async def test_cross_site_post_without_any_cookie_rejected(mcp_app, browser):
    """未ログイン・専用 Cookie なしの POST（クロスサイト / 直接送信）はハンドラで 403。"""
    await _make_user(mcp_app, username="xs3", password="CrossPass3")
    ticket = _ticket(mcp_app)
    for token in ("", "forged-token"):
        r = await browser.post(
            "/mcp-login/callback",
            data={
                "ticket": ticket,
                "csrf_token": token,
                "username": "xs3",
                "password": "CrossPass3",
            },
        )
        assert r.status_code == 403
        _assert_csp_safe_html(r)
        assert "<form" not in r.text
    assert _no_codes(mcp_app)
    # 偽造要求はレート制限カウンタにも影響しない（被害者アカウントのロックアウトに使えない）。
    assert await _failure_count(mcp_app, "xs3") == 0


async def test_csrf_missing_or_mismatch_shows_retry_link(mcp_app, browser):
    await _make_user(mcp_app, username="mm", password="MismatchPass1")
    ticket = _ticket(mcp_app)
    data = await _form(browser, ticket, username="mm", password="MismatchPass1")
    for token in (None, "", data["csrf_token"][:-1] + "A", "x" * 43):
        sent = {k: v for k, v in data.items() if k != "csrf_token"}
        if token is not None:
            sent["csrf_token"] = token
        r = await browser.post("/mcp-login/callback", data=sent)
        assert r.status_code == 403
        _assert_csp_safe_html(r)
        assert "ログイン画面からやり直してください" in r.text
        href = html.unescape(re.search(r'<a class="retry" href="([^"]*)"', r.text).group(1))
        assert href == "/mcp-login?" + urllib.parse.urlencode({"ticket": ticket})
    assert _no_codes(mcp_app)
    # 再試行リンクからフォームを取り直せば成功できる（再試行導線）。
    client_id = await _register_client_via_app(mcp_app)
    ticket2 = _ticket(mcp_app, client_id=client_id)
    retry = await _form(browser, ticket2, username="mm", password="MismatchPass1")
    _redirect_location(await browser.post("/mcp-login/callback", data=retry))


async def test_login_csrf_with_attacker_form_token_rejected(mcp_app):
    """攻撃者が自分のブラウザで得たトークン + 自分の資格情報を被害者ブラウザに送らせても拒否。"""
    await _make_user(mcp_app, username="attacker", password="AttackerPass1")
    client_id = await _register_client_via_app(mcp_app)
    ticket = _ticket(mcp_app, client_id=client_id)
    transport = ASGITransport(app=mcp_app)
    async with (
        AsyncClient(transport=transport, base_url="http://localhost") as attacker,
        AsyncClient(transport=transport, base_url="http://localhost") as victim,
    ):
        attacker_data = await _form(attacker, ticket, username="attacker", password="AttackerPass1")
        # 被害者ブラウザには被害者自身の（攻撃者が知らない）Cookie がある場合とない場合。
        r = await victim.post("/mcp-login/callback", data=attacker_data)
        assert r.status_code == 403
        await _form(victim, _ticket(mcp_app))
        assert victim.cookies.get(MCP_LOGIN_CSRF_COOKIE) != attacker_data["csrf_token"]
        r = await victim.post("/mcp-login/callback", data=attacker_data)
        assert r.status_code == 403
    assert _no_codes(mcp_app)


async def _register_client_via_app(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://localhost") as c:
        return await _register_client(c)


# --------------------------------------------------------------------------
# 認証失敗はフォームを本文エラー付きで再表示（JS 不要）
# --------------------------------------------------------------------------
async def test_bad_password_redisplays_form_with_error(mcp_app, browser):
    await _make_user(mcp_app, username="bp", password="RightPass1")
    ticket = _ticket(mcp_app)
    data = await _form(browser, ticket, username="bp", password="WrongPass")
    r = await browser.post("/mcp-login/callback", data=data)
    assert r.status_code == 401
    _assert_csp_safe_html(r)
    assert 'role="alert">ユーザー名またはパスワードが正しくありません。' in r.text
    # 同じ ticket・同じ CSRF トークンで再送できるフォームが再表示される。
    assert html.unescape(_TICKET_FIELD_RE.search(r.text).group(1)) == ticket
    assert _CSRF_FIELD_RE.search(r.text).group(1) == data["csrf_token"]
    assert 'name="username" value="bp"' in r.text
    assert 'value="WrongPass"' not in r.text  # パスワードは再表示しない
    # そのまま正しいパスワードで再送すると認証が通る（ticket は不正 client なので 400）。
    r2 = await browser.post("/mcp-login/callback", data={**data, "password": "RightPass1"})
    assert r2.status_code == 400
    _assert_csp_safe_html(r2)
    assert "認可要求が無効です" in r2.text


async def test_reflected_username_is_escaped(mcp_app, browser):
    evil = '"><script>alert(1)</script><a href="javascript:x'
    data = await _form(browser, _ticket(mcp_app), username=evil, password="whatever")
    r = await browser.post("/mcp-login/callback", data=data)
    assert r.status_code == 401
    _assert_csp_safe_html(r)
    assert f'name="username" value="{html.escape(evil)}"' in r.text


async def test_totp_mismatch_redisplays_form_with_error(mcp_app, browser):
    await _make_user(mcp_app, username="tt", password="TotpPass1")
    transport = ASGITransport(app=mcp_app)
    async with CsrfAwareClient(transport=transport, base_url="http://localhost") as admin:
        secret, _ = await _setup_and_enable_totp(admin, "tt", "TotpPass1")
    client_id = await _register_client_via_app(mcp_app)
    ticket = _ticket(mcp_app, client_id=client_id)
    data = await _form(browser, ticket, username="tt", password="TotpPass1")
    for code in ("", "000000"):
        r = await browser.post("/mcp-login/callback", data={**data, "code": code})
        assert r.status_code == 401
        _assert_csp_safe_html(r)
        assert "認証コードを確認して再度お試しください" in r.text
        assert _CSRF_FIELD_RE.search(r.text).group(1) == data["csrf_token"]
        assert 'name="username" value="tt"' in r.text
    assert _no_codes(mcp_app)
    ok = await browser.post("/mcp-login/callback", data={**data, "code": pyotp.TOTP(secret).now()})
    assert "code=" in _redirect_location(ok)


async def test_throttled_redisplays_form_with_error(tmp_path):
    app = create_app(_mcp_settings_with_throttle(tmp_path, max_attempts=2, lockout_seconds=90))
    async with app.router.lifespan_context(app):
        await _make_user(app, username="th", password="ThrottlePass1")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://localhost") as c:
            await _admin_session(c, "th", "ThrottlePass1")  # セッション Cookie ありでも同様
            data = await _form(c, _ticket(app), username="th", password="WrongPass")
            for _ in range(2):
                assert (await c.post("/mcp-login/callback", data=data)).status_code == 401
            r = await c.post("/mcp-login/callback", data=data)
            assert r.status_code == 429
            assert r.headers.get("Retry-After") == "90"
            _assert_csp_safe_html(r)
            assert "ログイン試行回数が上限を超えました" in r.text
            assert _CSRF_FIELD_RE.search(r.text).group(1) == data["csrf_token"]


async def test_expired_ticket_at_callback(mcp_app, browser, monkeypatch):
    await _make_user(mcp_app, username="ex", password="ExpiredPass1")
    data = await _form(browser, _ticket(mcp_app), username="ex", password="ExpiredPass1")
    # フォーム表示後に ticket の有効期限が過ぎた状況を再現する。
    monkeypatch.setattr("millicall.mcp_server.oauth.LOGIN_TICKET_TTL", -1)
    r = await browser.post("/mcp-login/callback", data=data)
    assert r.status_code == 400
    _assert_csp_safe_html(r)
    assert "不正または期限切れのログイン要求です" in r.text
    assert "<form" not in r.text
    assert 'class="retry"' not in r.text  # 期限切れ ticket では再試行リンクを出さない
    assert _no_codes(mcp_app)
    assert await _failure_count(mcp_app, "ex") == 0


def test_form_token_path_is_exact_match():
    """フォームトークン検証パスは完全一致のみ（配下の将来ルートを巻き込まない）。"""
    from millicall.auth.csrf import _is_exempt, _is_form_token_path

    assert _is_form_token_path("/mcp-login/callback") is True
    assert _is_form_token_path("/mcp-login/callback/") is False
    assert _is_form_token_path("/mcp-login/callback/x") is False
    assert _is_form_token_path("/mcp-login/callbackx") is False
    assert _is_form_token_path("/mcp-login") is False
    # 除外パスではない（ヘッダー検証の代わりにフォームトークン検証が必須）。
    assert _is_exempt("/mcp-login/callback") is False
