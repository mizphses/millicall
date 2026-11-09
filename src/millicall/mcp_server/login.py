"""MCP OAuth ログインページ + コールバック。

旧実装 (`../millicall-pbx/src/millicall/main.py:186-383`) の `/mcp-login` GET(HTML) と
`/mcp-login/callback` POST を v2 に移植したもの。DB ユーザーを `verify_password` で認証し、
role が許可集合（admin / user — コントローラ裁定#7）に含まれれば認可コードを発行して
`redirect_uri` へ遷移させる（302 ではなく meta refresh の HTML。_redirect_page 参照）。

秘密衛生: password は Form でのみ受け取りログ出力しない。存在しないユーザーにも Argon2 を
必ず実行してユーザー列挙を防ぐ（既存 auth/router.py と同方針）。

CSRF（issue #88）: 画面は JS を使わない通常の HTML form のため X-CSRF-Token ヘッダーを
送れない。GET /mcp-login が専用 Cookie を発行して同じ値を hidden `csrf_token` に埋め、
callback がセッション Cookie の有無に関係なく必ず照合する（auth/csrf.py 参照）。
エラーはインライン script（CSP で実行されない）ではなく HTML 本文に表示する。
"""

import html
import re
import urllib.parse

from fastapi import APIRouter, Form, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from millicall.app_settings.service import effective_settings
from millicall.audit import get_client_ip, record_audit
from millicall.auth.csrf import (
    MCP_LOGIN_CSRF_COOKIE,
    MCP_LOGIN_CSRF_COOKIE_PATH,
    generate_csrf_token,
    verify_form_csrf,
)
from millicall.auth.router import _verify_code
from millicall.auth.security import hash_password, verify_password
from millicall.auth.throttle import check_and_raise, clear_failures, record_failure
from millicall.mcp_server.oauth import LOGIN_TICKET_TTL
from millicall.models import User

router = APIRouter(tags=["mcp-oauth"])

# 許可ロール（コントローラ裁定#7: 既存 admin / user を許可、mcp ロール新設は Phase 6 送り）。
_ALLOWED_ROLES = frozenset({"admin", "user"})

# タイミング均一化用ダミーハッシュ（存在しないユーザーでも Argon2 を実行）。
_DUMMY_HASH = hash_password("millicall-mcp-dummy-timing-guard")

# ログインフォーム用 CSRF Cookie の寿命はログイン ticket と揃える。
_LOGIN_CSRF_MAX_AGE = LOGIN_TICKET_TTL
# generate_csrf_token()（token_urlsafe(32) = 43 文字）の形式。再利用時の妥当性確認に使う。
_CSRF_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{43}")

# ログイン画面の共通スタイル。CSP は style-src 'unsafe-inline' を許可しているが
# script-src は 'self' のみのため、画面は JavaScript を一切使わずに構成する（issue #88）。
_STYLE = """
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: system-ui, sans-serif; background: #f0eeeb; color: #1b1b1f;
    display: flex; justify-content: center; min-height: 100vh; padding-top: 80px; }
  .container { width: 100%; max-width: 400px; padding: 0 16px; }
  h1 { font-size: 21px; font-weight: 700; margin-bottom: 4px; }
  .subtitle { font-size: 13px; color: #4a4a52; margin-bottom: 24px; }
  .card { background: #fff; border: 1px solid #d4d2cd; border-radius: 5px; padding: 20px; }
  .form-group { margin-bottom: 16px; }
  label { display: block; font-size: 13px; font-weight: 600; margin-bottom: 6px; }
  input[type=text], input[type=password] { width: 100%; padding: 8px 10px; font-size: 14px;
    border: 1px solid #d4d2cd; border-radius: 5px; min-height: 38px; }
  button { width: 100%; padding: 10px 18px; font-size: 14px; border: none; border-radius: 5px;
    background: #c45d2c; color: #fff; min-height: 38px; cursor: pointer; }
  .mcp-badge { display: inline-block; font-size: 11px; font-weight: 600; color: #365a8a;
    background: rgba(54,90,138,0.08); border: 1px solid rgba(54,90,138,0.2);
    border-radius: 3px; padding: 2px 6px; margin-bottom: 16px; }
  .error { font-size: 13px; color: #9b1c1c; background: #fdf0f0; border: 1px solid #f0c4c4;
    border-radius: 5px; padding: 10px 12px; margin-bottom: 16px; }
  .retry { display: block; text-align: center; font-size: 14px; color: #c45d2c; }
"""

# エラーメッセージ（ユーザー列挙を避けるため認証失敗の文言は 1 種類に揃える）。
_MSG_BAD_CREDENTIALS = "ユーザー名またはパスワードが正しくありません。"
_MSG_DISABLED = "このアカウントは無効化されています。"
_MSG_FORBIDDEN_ROLE = "MCP接続の権限がありません。"
_MSG_TOTP = "2FA の登録と有効な認証コードが必要です。認証コードを確認して再度お試しください。"
_MSG_THROTTLED = "ログイン試行回数が上限を超えました。しばらく後に再試行してください。"
_MSG_CSRF = "ログイン画面の有効期限が切れたか、不正な送信です。ログイン画面からやり直してください。"
_MSG_BAD_TICKET = (
    "不正または期限切れのログイン要求です。MCP クライアントから接続をやり直してください。"
)
_MSG_BAD_AUTHZ = "認可要求が無効です。MCP クライアントから接続をやり直してください。"


def _document(body: str, *, head: str = "") -> str:
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">{head}
<title>Millicall PBX - MCP認証</title>
<style>{_STYLE}</style>
</head>
<body>
<div class="container">
{body}
</div>
</body>
</html>"""


def _login_page(*, ticket: str, csrf_token: str, error: str = "", username: str = "") -> str:
    """ログインフォーム。error があれば本文に表示する（ユーザー入力は必ずエスケープ）。"""
    e = html.escape
    error_html = f'<p class="error" role="alert">{e(error)}</p>' if error else ""
    # 失敗後の再表示ではユーザー名を残し、パスワード欄にフォーカスする。
    username_focus = "" if username else " autofocus"
    password_focus = " autofocus" if username else ""
    return _document(f"""  <h1>ログイン</h1>
  <p class="subtitle">Millicall PBXアカウントで認証してください</p>
  <div class="card">
    <div class="mcp-badge">MCP接続</div>
    {error_html}
    <form method="post" action="/mcp-login/callback">
      <input type="hidden" name="ticket" value="{e(ticket)}">
      <input type="hidden" name="csrf_token" value="{e(csrf_token)}">
      <div class="form-group">
        <label for="username">ユーザー名</label>
        <input type="text" id="username" name="username" value="{e(username)}"
          required{username_focus}>
      </div>
      <div class="form-group">
        <label for="password">パスワード</label>
        <input type="password" id="password" name="password" required{password_focus}>
      </div>
      <div class="form-group">
        <label for="code">認証コード（2FA 有効時）</label>
        <input type="text" id="code" name="code" autocomplete="one-time-code">
      </div>
      <button type="submit">認証</button>
    </form>
  </div>""")


def _message_page(*, message: str, retry_ticket: str | None = None) -> str:
    """フォームを再表示できないエラーの画面。ticket が有効なら再試行リンクを出す。"""
    e = html.escape
    retry_html = ""
    if retry_ticket is not None:
        href = "/mcp-login?" + urllib.parse.urlencode({"ticket": retry_ticket})
        retry_html = f'\n    <a class="retry" href="{e(href)}">ログイン画面に戻る</a>'
    return _document(f"""  <h1>ログイン</h1>
  <p class="subtitle">Millicall PBX - MCP接続</p>
  <div class="card">
    <p class="error" role="alert">{e(message)}</p>{retry_html}
  </div>""")


def _redirect_page(url: str) -> str:
    """認可完了後に MCP クライアントの redirect_uri へ移動させる画面。

    フォーム POST への 302 で別オリジンへ飛ばすと、ブラウザはフォームを含む画面の CSP
    `form-action 'self'` をリダイレクト先にも適用して遷移を止める（Chrome で確認）。
    CSP を緩めずに済むよう、POST には 200 の HTML を返し、meta refresh（フォーム送信では
    ないため form-action の対象外。JS も不要）と手動リンクで遷移させる。
    """
    e = html.escape
    return _document(
        f"""  <h1>認証が完了しました</h1>
  <p class="subtitle">MCP クライアントに戻ります。</p>
  <div class="card">
    <a class="retry" href="{e(url)}">自動で移動しない場合はこちら</a>
  </div>""",
        head=f'\n<meta http-equiv="refresh" content="0;url={e(url)}">',
    )


def _login_csrf_token(request: Request) -> str:
    """フォームに埋める CSRF トークン。既に妥当な Cookie があれば再利用する。

    再利用により、複数タブで並行して認可しても互いのフォームを無効化しない。
    """
    token = request.cookies.get(MCP_LOGIN_CSRF_COOKIE, "")
    return token if _CSRF_TOKEN_RE.fullmatch(token) else generate_csrf_token()


def _set_login_csrf_cookie(request: Request, response: Response, token: str) -> None:
    """ログインフォーム用の double-submit Cookie を発行する。

    HttpOnly（JS 不要・XSS で読ませない）/ SameSite=Strict（クロスサイト POST に付かない）/
    path=/mcp-login（他の画面へ送らない）。
    """
    settings = request.app.state.settings
    response.set_cookie(
        key=MCP_LOGIN_CSRF_COOKIE,
        value=token,
        max_age=_LOGIN_CSRF_MAX_AGE,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path=MCP_LOGIN_CSRF_COOKIE_PATH,
    )


def _clear_login_csrf_cookie(request: Request, response: Response) -> None:
    response.delete_cookie(
        key=MCP_LOGIN_CSRF_COOKIE,
        path=MCP_LOGIN_CSRF_COOKIE_PATH,
        secure=request.app.state.settings.cookie_secure,
        httponly=True,
        samesite="strict",
    )


def _verify_ticket(request: Request, ticket: str) -> dict | None:
    try:
        return request.app.state.mcp_oauth_provider.verify_login_ticket(ticket)
    except Exception:  # noqa: BLE001 — InvalidToken 含む全ての検証失敗
        return None


@router.get("/mcp-login", response_class=HTMLResponse, include_in_schema=False)
async def mcp_login_page(request: Request, ticket: str = Query(...)) -> HTMLResponse:
    """MCP OAuth 認可のためのログインフォームを表示する。

    認可パラメータは authorize() が署名した `ticket` にのみ封入され、
    フォームはそれをそのまま持ち回す（client 制御可能なフィールドは受け取らない）。
    未署名・改ざん・期限切れの ticket ではフォームも CSRF Cookie も出さない。
    """
    if _verify_ticket(request, ticket) is None:
        return HTMLResponse(
            _message_page(message=_MSG_BAD_TICKET), status_code=status.HTTP_400_BAD_REQUEST
        )
    csrf_token = _login_csrf_token(request)
    response = HTMLResponse(_login_page(ticket=ticket, csrf_token=csrf_token))
    _set_login_csrf_cookie(request, response, csrf_token)
    return response


@router.post("/mcp-login/callback", include_in_schema=False)
async def mcp_login_callback(
    request: Request,
    ticket: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    code: str = Form(default=""),
    csrf_token: str = Form(default=""),
):
    """ユーザーを認証し、認可コード付きで MCP クライアントへリダイレクトする。

    CSRF トークン（専用 Cookie と hidden フィールドの一致）を最初に検証し、不一致は 403。
    認可パラメータは署名済み `ticket` からのみ取得する。改ざん・期限切れは 400。
    認証失敗・TOTP 不一致・429 は同じ ticket のフォームをエラー付きで再表示する。
    """
    # CSRF はセッション Cookie の有無に関係なく必ず検証する（ログイン CSRF 対策を兼ねる）。
    # 資格情報検証・レート制限カウントより前に行い、偽造要求では何も状態を変えない。
    if not verify_form_csrf(request, csrf_token):
        retry_ticket = ticket if _verify_ticket(request, ticket) is not None else None
        return HTMLResponse(
            _message_page(message=_MSG_CSRF, retry_ticket=retry_ticket),
            status_code=status.HTTP_403_FORBIDDEN,
        )

    provider = request.app.state.mcp_oauth_provider
    claims = _verify_ticket(request, ticket)
    if claims is None:
        return HTMLResponse(
            _message_page(message=_MSG_BAD_TICKET), status_code=status.HTTP_400_BAD_REQUEST
        )

    def _retry(message: str, status_code: int, headers: dict | None = None) -> HTMLResponse:
        # 検証済みの csrf_token（= Cookie 値）と ticket でフォームを再表示する。
        return HTMLResponse(
            _login_page(ticket=ticket, csrf_token=csrf_token, error=message, username=username),
            status_code=status_code,
            headers=headers,
        )

    client_id = claims["client_id"]
    redirect_uri = claims["redirect_uri"]
    code_challenge = claims["code_challenge"]
    state = claims.get("state", "")
    scope_list = claims.get("scopes", [])
    resource = claims.get("resource", "")
    explicit = bool(claims.get("explicit", True))

    settings = await effective_settings(request.app.state)
    sessionmaker = request.app.state.sessionmaker
    ip = get_client_ip(request)

    async with sessionmaker() as session:
        # レート制限チェック（資格情報検証の前に行う）。
        try:
            await check_and_raise(
                session,
                ip=ip,
                username=username,
                ip_max_attempts=settings.login_max_attempts,
                username_max_attempts=settings.login_username_max_attempts,
                lockout_seconds=settings.login_lockout_seconds,
            )
        except HTTPException as exc:
            if exc.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
                return _retry(
                    _MSG_THROTTLED, status.HTTP_429_TOO_MANY_REQUESTS, headers=exc.headers
                )
            raise

        user = await session.scalar(select(User).where(User.username == username))
        check_hash = user.hashed_password if user is not None else _DUMMY_HASH
        password_ok = verify_password(check_hash, password)

        if user is None or not password_ok or user.origin != "local":
            await record_failure(session, ip=ip, username=username, action="mcp_login")
            await record_audit(
                session,
                actor_user_id=None,
                actor_label=username,
                action="mcp_login.failure",
                ip_address=ip,
            )
            await session.commit()
            return _retry(_MSG_BAD_CREDENTIALS, status.HTTP_401_UNAUTHORIZED)

        # 無効化されたユーザーは MCP OAuth も通さない（Phase 6 でユーザー無効化が追加された。
        # cookie セッションは get_current_user が enabled を見るが、MCP は別 Bearer 面のためここで明示）。
        if not user.enabled:
            await record_failure(session, ip=ip, username=username, action="mcp_login")
            await record_audit(
                session,
                actor_user_id=user.id,
                actor_label=username,
                action="mcp_login.failure",
                ip_address=ip,
            )
            await session.commit()
            return _retry(_MSG_DISABLED, status.HTTP_403_FORBIDDEN)

        if user.role not in _ALLOWED_ROLES:
            await record_failure(session, ip=ip, username=username, action="mcp_login")
            await record_audit(
                session,
                actor_user_id=user.id,
                actor_label=username,
                action="mcp_login.failure",
                ip_address=ip,
            )
            await session.commit()
            return _retry(_MSG_FORBIDDEN_ROLE, status.HTTP_403_FORBIDDEN)

        if (settings.totp_required and not user.totp_enabled) or (
            user.totp_enabled and _verify_code(user, request.app.state.secrets, code) is None
        ):
            await record_failure(session, ip=ip, username=username, action="mcp_totp")
            await record_audit(
                session,
                actor_user_id=user.id,
                actor_label=username,
                action="mcp_login.failure",
                ip_address=ip,
            )
            await session.commit()
            return _retry(_MSG_TOTP, status.HTTP_401_UNAUTHORIZED)

        user_state = (user.id, user.session_epoch, user.hashed_password)
        # ログイン成功: 全認証要素を検証した後にのみカウンタをリセット。
        await clear_failures(session, username=username)
        await record_audit(
            session,
            actor_user_id=user.id,
            actor_label=username,
            action="mcp_login.success",
            ip_address=ip,
        )
        await session.commit()

    try:
        code = provider.create_auth_code(
            client_id=client_id,
            username=username,
            code_challenge=code_challenge,
            redirect_uri=redirect_uri,
            scopes=scope_list,
            resource=resource or None,
            redirect_uri_provided_explicitly=explicit,
            user_state=user_state,
        )
    except ValueError:
        # client 未登録 / redirect_uri 未登録 / scope 不許可 は fail-closed。
        return HTMLResponse(
            _message_page(message=_MSG_BAD_AUTHZ), status_code=status.HTTP_400_BAD_REQUEST
        )

    params = {"code": code}
    if state:
        params["state"] = state
    separator = "&" if "?" in redirect_uri else "?"
    response = HTMLResponse(
        _redirect_page(f"{redirect_uri}{separator}{urllib.parse.urlencode(params)}")
    )
    # 認可完了後はフォーム用 CSRF Cookie を破棄する（次回の GET /mcp-login で再発行）。
    _clear_login_csrf_cookie(request, response)
    return response
