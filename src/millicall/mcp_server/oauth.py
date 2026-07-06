"""MCP OAuth 2.1 プロバイダ（インメモリ）。

旧実装 (`../millicall-pbx/src/millicall/mcp_server.py:63-240`) の `MillicallOAuthProvider`
の仕様を v2 / mcp SDK 1.28 の型 (`AuthorizationCode` / `AccessToken` / `RefreshToken`) に
載せ替えたもの。DCR(RFC7591) + PKCE(S256) をサポートし、クライアント登録・認可コード・
トークンをすべてプロセス内 dict に保持する（再起動で全失効 — コントローラ裁定#4）。

- access token: 24h / refresh token: 30d（旧実装同等）
- authorize は `<issuer>/mcp-login` へリダイレクトし、ログイン成功後に
  `create_auth_code()` で認可コードを発行する（実際のユーザー認証は login.py）。

秘密衛生: client_secret / access_token / refresh_token は本モジュールでログ出力しない。
"""

import json
import logging
import secrets
import time
import urllib.parse

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyHttpUrl, AnyUrl

from millicall.crypto import SecretBox

logger = logging.getLogger("millicall")

_AUTH_CODE_TTL = 600  # 10 分
_ACCESS_TTL = 86400  # 24 時間
_REFRESH_TTL = 86400 * 30  # 30 日
_LOGIN_TICKET_TTL = 600  # ログイン往復チケットの有効期限（10 分）


def _mask(token: str) -> str:
    """トークンをログ用にマスクする（先頭 6 文字 + 長さ）。"""
    return f"{token[:6]}…(len={len(token)})"


class MillicallOAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]
):
    """Millicall ユーザー DB を裏に持つ OAuth 2.1 プロバイダ（インメモリ）。"""

    def __init__(self, issuer_url: str) -> None:
        # issuer は authorize リダイレクト先 `<issuer>/mcp-login` の base に使う。
        self._issuer = issuer_url.rstrip("/")
        self._clients: dict[str, OAuthClientInformationFull] = {}
        self._auth_codes: dict[str, AuthorizationCode] = {}
        self._access_tokens: dict[str, AccessToken] = {}
        self._refresh_tokens: dict[str, RefreshToken] = {}
        # ログイン往復（authorize→/mcp-login→callback）の間、認可パラメータを
        # 改ざん不能に保持するための署名器。secrets ロード後に lifespan で注入する。
        self._signer: SecretBox | None = None

    def set_signer(self, signer: SecretBox) -> None:
        """認可パラメータ署名用の SecretBox を注入する（lifespan で secrets ロード後）。"""
        self._signer = signer

    def sign_login_ticket(self, payload: dict) -> str:
        """authorize パラメータを署名付き opaque トークンにする。"""
        if self._signer is None:
            raise RuntimeError("OAuth signer not configured")
        return self._signer.encrypt(json.dumps(payload, separators=(",", ":")))

    def verify_login_ticket(self, token: str) -> dict:
        """/mcp-login フォームから戻ってきたチケットを検証・展開する。

        改ざん・期限切れ（TTL 600s）は InvalidToken を送出（呼び出し側が 400 に変換）。
        """
        if self._signer is None:
            raise RuntimeError("OAuth signer not configured")
        return json.loads(self._signer.decrypt(token, ttl=_LOGIN_TICKET_TTL))

    # -- DCR (Dynamic Client Registration) --

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self._clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        assert client_info.client_id is not None
        self._clients[client_info.client_id] = client_info
        logger.info("MCP OAuth: registered client %s", client_info.client_id)

    # -- Authorization: ログインページへリダイレクト --

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        # 認可パラメータ一式を署名付きチケットに封入し、クエリには ticket だけを載せる。
        # これにより /mcp-login フォーム経由で client_id/redirect_uri/explicit 等を
        # クライアントが改ざんする経路を塞ぐ（open redirect / redirect_uri バインディング
        # バイパス対策）。redirect_uri は SDK 側 /authorize で client 登録値と照合済みだが、
        # チケットに封じた値を token 交換まで信頼の起点にする。
        ticket = self.sign_login_ticket(
            {
                "client_id": client.client_id,
                "redirect_uri": str(params.redirect_uri),
                "code_challenge": params.code_challenge,
                "state": params.state or "",
                "scopes": params.scopes or [],
                "resource": params.resource or "",
                "explicit": bool(params.redirect_uri_provided_explicitly),
            }
        )
        return f"{self._issuer}/mcp-login?{urllib.parse.urlencode({'ticket': ticket})}"

    # -- 認可コード --

    def create_auth_code(
        self,
        *,
        client_id: str,
        username: str,
        code_challenge: str,
        redirect_uri: str,
        scopes: list[str],
        resource: str | None = None,
        redirect_uri_provided_explicitly: bool = True,
    ) -> str:
        """ログイン成功後に呼び出し、認可コードを発行する（login.py から使用）。

        fail-closed: client が未登録、redirect_uri が登録値に含まれない、または
        scope が許可外の場合は ValueError を送出する（呼び出し側が 400 に変換）。
        """
        client = self._clients.get(client_id)
        if client is None:
            raise ValueError("unknown client")
        # SDK の検証器で redirect_uri（登録済みか）と scope（許可済みか）を照合。
        # 登録値は AnyUrl 型なので比較も AnyUrl で行う（AnyHttpUrl とは非等価）。
        # SDK 例外（InvalidRedirectUriError/InvalidScopeError）は ValueError へ正規化して
        # 呼び出し側（login.py）が一律 400 に変換できるようにする（fail-closed）。
        try:
            client.validate_redirect_uri(AnyUrl(redirect_uri))
            client.validate_scope(" ".join(scopes) if scopes else None)
        except ValueError:
            raise
        except Exception as exc:  # noqa: BLE001 — SDK の検証例外を ValueError に正規化
            raise ValueError(str(exc)) from exc
        code = secrets.token_urlsafe(32)
        self._auth_codes[code] = AuthorizationCode(
            code=code,
            scopes=scopes,
            expires_at=time.time() + _AUTH_CODE_TTL,
            client_id=client_id,
            code_challenge=code_challenge,
            redirect_uri=AnyHttpUrl(redirect_uri),
            redirect_uri_provided_explicitly=redirect_uri_provided_explicitly,
            resource=resource or None,
            subject=username,
        )
        return code

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        code = self._auth_codes.get(authorization_code)
        if code is None or code.client_id != client.client_id:
            return None
        if time.time() > code.expires_at:
            self._auth_codes.pop(authorization_code, None)
            return None
        return code

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        self._auth_codes.pop(authorization_code.code, None)
        assert client.client_id is not None

        access = secrets.token_urlsafe(48)
        refresh = secrets.token_urlsafe(48)
        now = time.time()
        self._access_tokens[access] = AccessToken(
            token=access,
            client_id=client.client_id,
            scopes=authorization_code.scopes,
            expires_at=int(now + _ACCESS_TTL),
            resource=authorization_code.resource,
            subject=authorization_code.subject,
        )
        self._refresh_tokens[refresh] = RefreshToken(
            token=refresh,
            client_id=client.client_id,
            scopes=authorization_code.scopes,
            expires_at=int(now + _REFRESH_TTL),
            subject=authorization_code.subject,
        )
        logger.info(
            "MCP OAuth: issued tokens for subject=%s access=%s",
            authorization_code.subject,
            _mask(access),
        )
        return OAuthToken(
            access_token=access,
            refresh_token=refresh,
            expires_in=_ACCESS_TTL,
            scope=" ".join(authorization_code.scopes) if authorization_code.scopes else None,
        )

    # -- リフレッシュトークン --

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        token = self._refresh_tokens.get(refresh_token)
        if token is None or token.client_id != client.client_id:
            return None
        if token.expires_at is not None and time.time() > token.expires_at:
            self._refresh_tokens.pop(refresh_token, None)
            return None
        return token

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        self._refresh_tokens.pop(refresh_token.token, None)
        assert client.client_id is not None

        use_scopes = scopes or refresh_token.scopes
        access = secrets.token_urlsafe(48)
        refresh = secrets.token_urlsafe(48)
        now = time.time()
        self._access_tokens[access] = AccessToken(
            token=access,
            client_id=client.client_id,
            scopes=use_scopes,
            expires_at=int(now + _ACCESS_TTL),
            subject=refresh_token.subject,
        )
        self._refresh_tokens[refresh] = RefreshToken(
            token=refresh,
            client_id=client.client_id,
            scopes=use_scopes,
            expires_at=int(now + _REFRESH_TTL),
            subject=refresh_token.subject,
        )
        return OAuthToken(
            access_token=access,
            refresh_token=refresh,
            expires_in=_ACCESS_TTL,
            scope=" ".join(use_scopes) if use_scopes else None,
        )

    # -- アクセストークン検証 --

    async def load_access_token(self, token: str) -> AccessToken | None:
        stored = self._access_tokens.get(token)
        if stored is None:
            return None
        if stored.expires_at is not None and time.time() >= stored.expires_at:
            self._access_tokens.pop(token, None)
            return None
        return stored

    # -- 失効 --

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        self._access_tokens.pop(token.token, None)
        self._refresh_tokens.pop(token.token, None)
