"""MCP OAuth ログインページ + コールバック。

旧実装 (`../millicall-pbx/src/millicall/main.py:186-383`) の `/mcp-login` GET(HTML) と
`/mcp-login/callback` POST を v2 に移植したもの。DB ユーザーを `verify_password` で認証し、
role が許可集合（admin / user — コントローラ裁定#7）に含まれれば認可コードを発行して
`redirect_uri` へリダイレクトする。

秘密衛生: password は Form でのみ受け取りログ出力しない。存在しないユーザーにも Argon2 を
必ず実行してユーザー列挙を防ぐ（既存 auth/router.py と同方針）。
"""

import html
import urllib.parse

from fastapi import APIRouter, Form, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from millicall.auth.security import hash_password, verify_password
from millicall.models import User

router = APIRouter(tags=["mcp-oauth"])

# 許可ロール（コントローラ裁定#7: 既存 admin / user を許可、mcp ロール新設は Phase 6 送り）。
_ALLOWED_ROLES = frozenset({"admin", "user"})

# タイミング均一化用ダミーハッシュ（存在しないユーザーでも Argon2 を実行）。
_DUMMY_HASH = hash_password("millicall-mcp-dummy-timing-guard")


def _login_page(*, ticket: str) -> str:
    e = html.escape
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Millicall PBX - MCP認証</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: system-ui, sans-serif; background: #f0eeeb; color: #1b1b1f;
    display: flex; justify-content: center; min-height: 100vh; padding-top: 80px; }}
  .container {{ width: 100%; max-width: 400px; padding: 0 16px; }}
  h1 {{ font-size: 21px; font-weight: 700; margin-bottom: 4px; }}
  .subtitle {{ font-size: 13px; color: #4a4a52; margin-bottom: 24px; }}
  .card {{ background: #fff; border: 1px solid #d4d2cd; border-radius: 5px; padding: 20px; }}
  .form-group {{ margin-bottom: 16px; }}
  label {{ display: block; font-size: 13px; font-weight: 600; margin-bottom: 6px; }}
  input[type=text], input[type=password] {{ width: 100%; padding: 8px 10px; font-size: 14px;
    border: 1px solid #d4d2cd; border-radius: 5px; min-height: 38px; }}
  button {{ width: 100%; padding: 10px 18px; font-size: 14px; border: none; border-radius: 5px;
    background: #c45d2c; color: #fff; min-height: 38px; cursor: pointer; }}
  .mcp-badge {{ display: inline-block; font-size: 11px; font-weight: 600; color: #365a8a;
    background: rgba(54,90,138,0.08); border: 1px solid rgba(54,90,138,0.2);
    border-radius: 3px; padding: 2px 6px; margin-bottom: 16px; }}
</style>
</head>
<body>
<div class="container">
  <h1>ログイン</h1>
  <p class="subtitle">Millicall PBXアカウントで認証してください</p>
  <div class="card">
    <div class="mcp-badge">MCP接続</div>
    <form method="post" action="/mcp-login/callback">
      <input type="hidden" name="ticket" value="{e(ticket)}">
      <div class="form-group">
        <label for="username">ユーザー名</label>
        <input type="text" id="username" name="username" required autofocus>
      </div>
      <div class="form-group">
        <label for="password">パスワード</label>
        <input type="password" id="password" name="password" required>
      </div>
      <button type="submit">認証</button>
    </form>
  </div>
</div>
</body>
</html>"""


@router.get("/mcp-login", response_class=HTMLResponse, include_in_schema=False)
async def mcp_login_page(ticket: str = Query(...)) -> HTMLResponse:
    """MCP OAuth 認可のためのログインフォームを表示する。

    認可パラメータは authorize() が署名した `ticket` にのみ封入され、
    フォームはそれをそのまま持ち回す（client 制御可能なフィールドは受け取らない）。
    """
    return HTMLResponse(_login_page(ticket=ticket))


@router.post("/mcp-login/callback", include_in_schema=False)
async def mcp_login_callback(
    request: Request,
    ticket: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
):
    """ユーザーを認証し、認可コード付きで MCP クライアントへリダイレクトする。

    認可パラメータは署名済み `ticket` からのみ取得する。改ざん・期限切れは 400。
    """
    provider = request.app.state.mcp_oauth_provider
    try:
        claims = provider.verify_login_ticket(ticket)
    except Exception:  # noqa: BLE001 — InvalidToken 含む全ての検証失敗
        return HTMLResponse(
            "<html><body>不正または期限切れのログイン要求です。</body></html>",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    client_id = claims["client_id"]
    redirect_uri = claims["redirect_uri"]
    code_challenge = claims["code_challenge"]
    state = claims.get("state", "")
    scope_list = claims.get("scopes", [])
    resource = claims.get("resource", "")
    explicit = bool(claims.get("explicit", True))

    sessionmaker = request.app.state.sessionmaker
    async with sessionmaker() as session:
        user = await session.scalar(select(User).where(User.username == username))
        check_hash = user.hashed_password if user is not None else _DUMMY_HASH
        password_ok = verify_password(check_hash, password)

    if user is None or not password_ok:
        return HTMLResponse(
            "<html><body><script>"
            'alert("ユーザー名またはパスワードが正しくありません");history.back();'
            "</script></body></html>",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    if user.role not in _ALLOWED_ROLES:
        return HTMLResponse(
            "<html><body><script>"
            'alert("MCP接続の権限がありません");history.back();'
            "</script></body></html>",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    try:
        code = provider.create_auth_code(
            client_id=client_id,
            username=username,
            code_challenge=code_challenge,
            redirect_uri=redirect_uri,
            scopes=scope_list,
            resource=resource or None,
            redirect_uri_provided_explicitly=explicit,
        )
    except ValueError:
        # client 未登録 / redirect_uri 未登録 / scope 不許可 は fail-closed。
        return HTMLResponse(
            "<html><body>認可要求が無効です。</body></html>",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    params = {"code": code}
    if state:
        params["state"] = state
    separator = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(
        url=f"{redirect_uri}{separator}{urllib.parse.urlencode(params)}",
        status_code=status.HTTP_302_FOUND,
    )
