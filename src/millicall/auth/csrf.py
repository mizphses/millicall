"""CSRF 保護ミドルウェア（double-submit cookie パターン）。

仕組み:
  - ログイン成功時にセッション Cookie と同時に non-HttpOnly な csrf Cookie を発行する。
  - 状態変更リクエスト（POST/PUT/PATCH/DELETE）かつセッション Cookie を持つリクエストには
    X-CSRF-Token ヘッダーを必須とし、csrf Cookie の値と定数時間比較する。
  - セッション Cookie を持たないリクエスト（Bearer 認証など）は免除する。
  - ログアウト時は csrf Cookie を削除する。

除外パス（CSRF チェックをスキップ):
  /api/auth/login     — セッションなし; pre-auth エンドポイント
  /api/auth/login/totp — 同上
  /saml/              — IdP から cross-origin POST が来る（T4/T5 で使用）
  /scim/              — Bearer 認証; Cookie を使わない
  /mcp                — OAuth Bearer 認証

GET / HEAD / OPTIONS は常に免除する。
"""
import hmac
import secrets

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

# CSRF チェックが不要なパス（pre-auth または非 Cookie 認証）。
# レビュー M-1/M-2: 素の startswith は `/mcp-login/callback` や将来の
# `/api/auth/login-history` 等を意図せず免除してしまう。境界を意識した一致
# （完全一致 または prefix + "/" で始まる）に限定する。
_EXEMPT_PREFIXES = (
    "/api/auth/login",      # POST /api/auth/login（完全一致）
    "/api/auth/login/totp",  # 2 段階ログイン
    "/saml",                # SAML IdP からの cross-origin POST（T4/T5）
    "/scim",                # SCIM (Bearer auth); Cookie を使わない
    "/mcp",                 # MCP OAuth Bearer
)

# CSRF チェック対象のメソッド（状態変更リクエスト）
_CHECKED_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _is_exempt(path: str) -> bool:
    """パスが CSRF チェック除外対象かどうかを返す（境界一致）。"""
    return any(
        path == prefix or path.startswith(prefix + "/") for prefix in _EXEMPT_PREFIXES
    )


class CsrfMiddleware(BaseHTTPMiddleware):
    """double-submit cookie パターンによる CSRF 保護ミドルウェア。"""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:
        settings = request.app.state.settings
        cookie_name: str = settings.csrf_cookie_name
        session_cookie_name: str = settings.session_cookie_name

        # CSRF チェック対象か判定
        if (
            request.method in _CHECKED_METHODS
            and not _is_exempt(request.url.path)
            and request.cookies.get(session_cookie_name)  # セッション Cookie あり = Cookie 認証
        ):
            csrf_cookie = request.cookies.get(cookie_name)
            csrf_header = request.headers.get("X-CSRF-Token")

            # どちらか欠けている、または値が一致しない場合は 403
            if not csrf_cookie or not csrf_header or not hmac.compare_digest(csrf_cookie, csrf_header):
                return JSONResponse(
                    status_code=403,
                    content={"detail": "CSRF token missing or invalid"},
                )

        return await call_next(request)


def set_csrf_cookie(response: Response, settings, token: str) -> None:
    """CSRF トークンを non-HttpOnly Cookie にセットする。

    セッション Cookie と同じ Secure / SameSite 属性を使う。
    httponly=False にすることで JS がトークンを読み取れる。
    """
    response.set_cookie(
        key=settings.csrf_cookie_name,
        value=token,
        max_age=settings.session_max_age,
        httponly=False,  # JS から読み取れる必要がある
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        path="/",
    )


def clear_csrf_cookie(response: Response, settings) -> None:
    """ログアウト時に CSRF Cookie を削除する（set 時と同じ属性で厳密に削除）。"""
    response.delete_cookie(
        key=settings.csrf_cookie_name,
        path="/",
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
    )


def generate_csrf_token() -> str:
    """暗号学的に安全なランダムトークンを生成する。"""
    return secrets.token_urlsafe(32)
