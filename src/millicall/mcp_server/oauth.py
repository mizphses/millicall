"""MCP OAuth 2.1 プロバイダ（SQLite 共有ストア）。

旧実装 (`../millicall-pbx/src/millicall/mcp_server.py:63-240`) の `MillicallOAuthProvider`
の仕様を v2 / mcp SDK 2.x の型 (`AuthorizationCode` / `AccessToken` / `RefreshToken`) に
載せ替えたもの。DCR(RFC7591) + PKCE(S256) をサポートする。

状態の保存先（issue #86）: クライアント登録・認可コード・トークンは DB
（mcp_oauth_clients / mcp_oauth_codes / mcp_oauth_tokens）に保存する。再起動しても
認可は失効せず、同じ DB を共有する複数プロセスのどこにリクエストが届いても同じ結果になる。

- access token: 24h / refresh token: 30d（旧実装同等）
- authorize は `<issuer>/mcp-login` へリダイレクトし、ログイン成功後に
  `create_auth_code()` で認可コードを発行する（実際のユーザー認証は login.py）。
- 一回限りの消費（認可コード交換・リフレッシュトークンのローテーション）は
  `DELETE ... RETURNING` の単一文で行う。SQLite の書き込みロックで直列化されるため、
  複数プロセスが同時に同じ値を出しても行を受け取れるのは 1 つだけ。
- 期限切れ行はアクセス時に遅延削除し、書き込み時（登録・発行）に一括削除する。
- DCR は未認証で叩けるため、登録数とメタデータサイズに上限を設ける。

秘密衛生: client_secret / 認可コード / access_token / refresh_token は SHA-256 ハッシュのみを
DB に保存し、平文は保存・ログ出力しない（DB ダンプやバックアップから有効な値を復元できない）。
高エントロピーの乱数なのでソルト無しハッシュで十分（パスワードとは扱いが異なる）。
"""

import base64
import binascii
import hashlib
import hmac
import json
import logging
import secrets
import time
import urllib.parse
from collections.abc import Iterable
from urllib.parse import unquote

from mcp.server.auth.handlers.token import TokenHandler
from mcp.server.auth.middleware.client_auth import AuthenticationError, ClientAuthenticator
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    RegistrationError,
    TokenError,
    TokenVerifier,
)
from mcp.server.auth.routes import cors_middleware
from mcp.server.streamable_http_manager import (
    DEFAULT_MAX_REQUEST_BODY_SIZE,
    RequestBodyLimitMiddleware,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyHttpUrl, AnyUrl, ValidationError
from sqlalchemy import and_, delete, func, or_, select, update
from starlette.requests import Request
from starlette.routing import BaseRoute, Route

from millicall.crypto import SecretBox
from millicall.models import McpOAuthClient, McpOAuthCode, McpOAuthToken

logger = logging.getLogger("millicall")

_AUTH_CODE_TTL = 600  # 10 分
_ACCESS_TTL = 86400  # 24 時間
_REFRESH_TTL = 86400 * 30  # 30 日
LOGIN_TICKET_TTL = 600  # ログイン往復チケットの有効期限（10 分）

# DCR の上限。/register は未認証で叩けるため、テーブルを無制限に膨らませない。
_MAX_CLIENTS = 1000
# 登録後一度も認可（コード発行）されないクライアントの保持期間。
_UNUSED_CLIENT_TTL = 86400  # 24 時間
# 最後の認可/トークン発行からの保持期間（refresh TTL より長く取り、それ以降は再登録させる）。
_IDLE_CLIENT_TTL = 86400 * 90  # 90 日
# クライアントメタデータ（JSON）の最大サイズ。SDK の本文上限（4 MiB）より十分小さくする。
_MAX_CLIENT_INFO_BYTES = 16 * 1024
# subject（ユーザー）あたりの有効トークン上限。超過時は古いものから失効させる。
_MAX_REFRESH_PER_SUBJECT = 50
_MAX_ACCESS_PER_SUBJECT = 200

# 発行時アカウント状態: (User.id, session_epoch, hashed_password)。login.py から渡される形。
UserState = tuple[int, int, str]


def _mask(token: str) -> str:
    """トークンをログ用にマスクする（先頭 6 文字 + 長さ）。"""
    return f"{token[:6]}…(len={len(token)})"


def _digest(value: str) -> str:
    """高エントロピーの秘密値（コード/トークン/client_secret）の保存用ハッシュ。"""
    return hashlib.sha256(value.encode()).hexdigest()


def _credential_tag(password_hash: str) -> str:
    """資格情報（パスワードハッシュ）変更検知用のタグ。ハッシュそのものは複製しない。"""
    return hashlib.sha256(password_hash.encode()).hexdigest()


class MillicallOAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]
):
    """Millicall ユーザー DB を裏に持つ OAuth 2.1 プロバイダ（DB 共有ストア）。"""

    def __init__(self, issuer_url: str, app_state=None) -> None:
        # issuer は authorize リダイレクト先 `<issuer>/mcp-login` の base に使う。
        self._issuer = issuer_url.rstrip("/")
        self._app_state = app_state
        # ログイン往復（authorize→/mcp-login→callback）の間、認可パラメータを
        # 改ざん不能に保持するための署名器。secrets ロード後に lifespan で注入する。
        self._signer: SecretBox | None = None
        # 状態ストア兼 User.session_epoch/enabled 照合用の DB セッションファクトリ
        # （lifespan で注入）。未注入のまま使うと RuntimeError（fail-closed）。
        self._sessionmaker = None

    def set_signer(self, signer: SecretBox) -> None:
        """認可パラメータ署名用の SecretBox を注入する（lifespan で secrets ロード後）。"""
        self._signer = signer

    def set_sessionmaker(self, sessionmaker) -> None:
        """状態ストア兼 User.enabled/session_epoch 照合用の sessionmaker を注入する（lifespan）。"""
        self._sessionmaker = sessionmaker

    def _session(self):
        if self._sessionmaker is None:
            raise RuntimeError("OAuth state store not configured")
        return self._sessionmaker()

    async def _current_user_state(self, subject: str) -> tuple[bool, UserState] | None:
        """subject の有効状態とアカウント資格情報を返す。不在時は None。"""
        from millicall.models import User

        async with self._session() as session:
            user = await session.scalar(select(User).where(User.username == subject))
        if user is None:
            return None
        if self._app_state is not None:
            from millicall.app_settings.service import effective_settings

            settings = await effective_settings(self._app_state)
            if settings.totp_required and not user.totp_enabled:
                return None
        return bool(user.enabled), (int(user.id), int(user.session_epoch), user.hashed_password)

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
        return json.loads(self._signer.decrypt(token, ttl=LOGIN_TICKET_TTL))

    # -- 清掃 --

    @staticmethod
    async def _purge_expired(session, now: float) -> None:
        """期限切れのコード/トークンと放置クライアントを一括削除する（書き込み txn 内で呼ぶ）。

        クライアント削除はコード/トークンへ ON DELETE CASCADE で波及する。
        """
        await session.execute(delete(McpOAuthCode).where(McpOAuthCode.expires_at <= now))
        await session.execute(delete(McpOAuthToken).where(McpOAuthToken.expires_at <= now))
        await session.execute(
            delete(McpOAuthClient).where(
                or_(
                    and_(
                        McpOAuthClient.last_used_at.is_(None),
                        McpOAuthClient.created_at <= now - _UNUSED_CLIENT_TTL,
                    ),
                    McpOAuthClient.last_used_at <= now - _IDLE_CLIENT_TTL,
                )
            )
        )

    # -- DCR (Dynamic Client Registration) --

    async def _load_client(
        self, client_id: str
    ) -> tuple[OAuthClientInformationFull, str | None] | None:
        """登録情報と client_secret ハッシュを返す（HashedSecretClientAuthenticator 用）。"""
        async with self._session() as session:
            row = await session.get(McpOAuthClient, client_id)
        if row is None:
            return None
        return OAuthClientInformationFull.model_validate_json(row.client_info), (
            row.client_secret_hash
        )

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        loaded = await self._load_client(client_id)
        if loaded is None:
            return None
        client, secret_hash = loaded
        if secret_hash is not None:
            # 平文は保存していない。SDK 標準の ClientAuthenticator が万一使われても
            # 一致し得ない値を入れて fail-closed にする（/token は HashedSecretClientAuthenticator
            # がハッシュ照合する — install_hashed_client_auth 参照）。
            client.client_secret = secrets.token_hex(32)
        return client

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        assert client_info.client_id is not None
        stored_info = client_info.model_dump_json(exclude={"client_secret"})
        if len(stored_info.encode()) > _MAX_CLIENT_INFO_BYTES:
            raise RegistrationError("invalid_client_metadata", "Client metadata too large")
        secret_hash = _digest(client_info.client_secret) if client_info.client_secret else None
        now = time.time()
        async with self._session() as session:
            # 先頭の DELETE で書き込みロックを取り、件数確認〜INSERT を他プロセスと直列化する。
            await self._purge_expired(session, now)
            count = await session.scalar(select(func.count()).select_from(McpOAuthClient)) or 0
            if count >= _MAX_CLIENTS:
                # 一度も認可されていない登録（攻撃者が量産できるのはこれだけ）を古い順に追い出す。
                victims = (
                    select(McpOAuthClient.client_id)
                    .where(McpOAuthClient.last_used_at.is_(None))
                    .order_by(McpOAuthClient.created_at)
                    .limit(count - _MAX_CLIENTS + 1)
                )
                await session.execute(
                    delete(McpOAuthClient).where(McpOAuthClient.client_id.in_(victims))
                )
                count = await session.scalar(select(func.count()).select_from(McpOAuthClient)) or 0
                if count >= _MAX_CLIENTS:
                    await session.rollback()
                    logger.warning("MCP OAuth: client registration limit reached")
                    raise RegistrationError(
                        "invalid_client_metadata", "Client registration limit reached"
                    )
            session.add(
                McpOAuthClient(
                    client_id=client_info.client_id,
                    client_secret_hash=secret_hash,
                    client_info=stored_info,
                    created_at=now,
                    last_used_at=None,
                )
            )
            await session.commit()
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

    async def create_auth_code(
        self,
        *,
        client_id: str,
        username: str,
        code_challenge: str,
        redirect_uri: str,
        scopes: list[str],
        resource: str | None = None,
        redirect_uri_provided_explicitly: bool = True,
        user_state: UserState | None = None,
    ) -> str:
        """ログイン成功後に呼び出し、認可コードを発行する（login.py から使用）。

        fail-closed: client が未登録、redirect_uri が登録値に含まれない、scope が許可外、
        または認証済みユーザー状態が無い場合は ValueError を送出する（呼び出し側が 400 に変換）。
        """
        client = await self.get_client(client_id)
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
        if user_state is None:
            raise ValueError("Authenticated user state required")
        if resource and str(AnyHttpUrl(resource)).rstrip("/") != str(
            AnyHttpUrl(self._issuer)
        ).rstrip("/"):
            raise ValueError("Invalid token resource")
        AnyHttpUrl(redirect_uri)  # 交換時に AnyHttpUrl で復元するため発行前に検証する
        code = secrets.token_urlsafe(32)
        now = time.time()
        user_id, epoch, password_hash = user_state
        async with self._session() as session:
            # 認可の発生を記録する（未使用登録として清掃・追い出しされないようにする）。
            touched = await session.execute(
                update(McpOAuthClient)
                .where(McpOAuthClient.client_id == client_id)
                .values(last_used_at=now)
            )
            if touched.rowcount != 1:
                await session.rollback()
                raise ValueError("unknown client")
            await session.execute(delete(McpOAuthCode).where(McpOAuthCode.expires_at <= now))
            session.add(
                McpOAuthCode(
                    code_hash=_digest(code),
                    client_id=client_id,
                    subject=username,
                    user_id=user_id,
                    session_epoch=epoch,
                    credential_tag=_credential_tag(password_hash),
                    code_challenge=code_challenge,
                    redirect_uri=redirect_uri,
                    redirect_uri_provided_explicitly=redirect_uri_provided_explicitly,
                    scopes=json.dumps(scopes),
                    resource=resource or self._issuer,
                    expires_at=now + _AUTH_CODE_TTL,
                )
            )
            await session.commit()
        return code

    @staticmethod
    def _code_from_row(row, code: str) -> AuthorizationCode:
        return AuthorizationCode(
            code=code,
            scopes=json.loads(row.scopes),
            expires_at=row.expires_at,
            client_id=row.client_id,
            code_challenge=row.code_challenge,
            redirect_uri=AnyHttpUrl(row.redirect_uri),
            redirect_uri_provided_explicitly=row.redirect_uri_provided_explicitly,
            resource=row.resource,
            subject=row.subject,
        )

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        code_hash = _digest(authorization_code)
        async with self._session() as session:
            row = await session.get(McpOAuthCode, code_hash)
            if row is None or row.client_id != client.client_id:
                return None
            if time.time() > row.expires_at:
                await session.execute(
                    delete(McpOAuthCode).where(McpOAuthCode.code_hash == code_hash)
                )
                await session.commit()
                return None
        return self._code_from_row(row, authorization_code)

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        code_hash = _digest(authorization_code.code)
        # ユーザー状態の確認は読み取りだけで先に済ませる（書き込みロックを持ったまま
        # 別接続で User を読まない）。消費は下の DELETE で確認済みの発行時状態に限定する。
        async with self._session() as session:
            peek = await session.get(McpOAuthCode, code_hash)
        if peek is None or peek.client_id != client.client_id:
            raise TokenError("invalid_grant", "Authorization code no longer valid")
        issued_state = (peek.user_id, peek.session_epoch, peek.credential_tag)
        await self._validate_grant_state(peek.subject, issued_state)
        assert client.client_id is not None

        # 一回限りの消費: 条件付き DELETE ... RETURNING を最初の文にして書き込みロックを取り、
        # 新トークンの INSERT まで同一トランザクションで commit する。並行交換では片方だけが
        # 行を得る。発行側で失敗した場合はロールバックされ、コードは消費されない。
        async with self._session() as session:
            result = await session.execute(
                delete(McpOAuthCode)
                .where(
                    McpOAuthCode.code_hash == code_hash,
                    McpOAuthCode.client_id == client.client_id,
                    McpOAuthCode.user_id == issued_state[0],
                    McpOAuthCode.session_epoch == issued_state[1],
                    McpOAuthCode.credential_tag == issued_state[2],
                )
                .returning(*McpOAuthCode.__table__.columns)
            )
            row = result.first()
            if (
                row is None
                or self._code_from_row(row, authorization_code.code) != authorization_code
                or time.time() >= row.expires_at
            ):
                await session.rollback()
                raise TokenError("invalid_grant", "Authorization code no longer valid")
            scopes = json.loads(row.scopes)
            access, refresh = await self._issue_tokens(
                session,
                client_id=client.client_id,
                subject=row.subject,
                scopes=scopes,
                resource=row.resource,
                issued_state=issued_state,
                grant_id=secrets.token_hex(16),
            )
            await session.commit()
        logger.info(
            "MCP OAuth: issued tokens for subject=%s access=%s",
            row.subject,
            _mask(access),
        )
        return OAuthToken(
            access_token=access,
            refresh_token=refresh,
            expires_in=_ACCESS_TTL,
            scope=" ".join(scopes) if scopes else None,
        )

    async def _issue_tokens(
        self,
        session,
        *,
        client_id: str,
        subject: str,
        scopes: list[str],
        resource: str | None,
        issued_state: tuple[int, int, str],
        grant_id: str,
    ) -> tuple[str, str]:
        """access/refresh を呼び出し側の書き込みトランザクション内で保存し、平文を返す。

        commit は呼び出し側の責務（旧トークンの消費と同一トランザクションにするため）。
        平文は DB に残らない。
        """
        access = secrets.token_urlsafe(48)
        refresh = secrets.token_urlsafe(48)
        now = time.time()
        user_id, epoch, credential_tag = issued_state
        common = dict(
            grant_id=grant_id,
            client_id=client_id,
            subject=subject,
            user_id=user_id,
            session_epoch=epoch,
            credential_tag=credential_tag,
            scopes=json.dumps(scopes),
            resource=resource,
            created_at=now,
        )
        # 清掃でクライアントが消えていたら発行しない（呼び出し側の txn ごとロールバック）。
        touched = await session.execute(
            update(McpOAuthClient)
            .where(McpOAuthClient.client_id == client_id)
            .values(last_used_at=now)
        )
        if touched.rowcount != 1:
            await session.rollback()
            raise TokenError("invalid_grant", "Client no longer registered")
        await self._purge_expired(session, now)
        await self._enforce_subject_caps(session, subject)
        session.add_all(
            [
                McpOAuthToken(
                    token_hash=_digest(access),
                    kind="access",
                    expires_at=now + _ACCESS_TTL,
                    **common,
                ),
                McpOAuthToken(
                    token_hash=_digest(refresh),
                    kind="refresh",
                    expires_at=now + _REFRESH_TTL,
                    **common,
                ),
            ]
        )
        await session.flush()
        return access, refresh

    @staticmethod
    async def _enforce_subject_caps(session, subject: str) -> None:
        """subject あたりの有効トークン数を上限内に収める（新規 1 組の発行前に呼ぶ）。

        refresh 超過時は最も古い認可（grant）を access ごと失効させ、access 超過時は
        最も古い access から失効させる。
        """
        refresh_count = (
            await session.scalar(
                select(func.count())
                .select_from(McpOAuthToken)
                .where(McpOAuthToken.subject == subject, McpOAuthToken.kind == "refresh")
            )
            or 0
        )
        if refresh_count >= _MAX_REFRESH_PER_SUBJECT:
            old_grants = (
                select(McpOAuthToken.grant_id)
                .where(McpOAuthToken.subject == subject, McpOAuthToken.kind == "refresh")
                .order_by(McpOAuthToken.created_at)
                .limit(refresh_count - _MAX_REFRESH_PER_SUBJECT + 1)
            )
            await session.execute(
                delete(McpOAuthToken).where(McpOAuthToken.grant_id.in_(old_grants))
            )
        access_count = (
            await session.scalar(
                select(func.count())
                .select_from(McpOAuthToken)
                .where(McpOAuthToken.subject == subject, McpOAuthToken.kind == "access")
            )
            or 0
        )
        if access_count >= _MAX_ACCESS_PER_SUBJECT:
            old_access = (
                select(McpOAuthToken.token_hash)
                .where(McpOAuthToken.subject == subject, McpOAuthToken.kind == "access")
                .order_by(McpOAuthToken.created_at)
                .limit(access_count - _MAX_ACCESS_PER_SUBJECT + 1)
            )
            await session.execute(
                delete(McpOAuthToken).where(McpOAuthToken.token_hash.in_(old_access))
            )

    # -- リフレッシュトークン --

    @staticmethod
    def _refresh_from_row(row, token: str) -> RefreshToken:
        return RefreshToken(
            token=token,
            client_id=row.client_id,
            scopes=json.loads(row.scopes),
            expires_at=int(row.expires_at),
            resource=row.resource,
            subject=row.subject,
        )

    async def _load_token_row(self, token: str, kind: str):
        """有効期限内のトークン行を返す。期限切れはその場で削除して None。"""
        token_hash = _digest(token)
        async with self._session() as session:
            row = await session.get(McpOAuthToken, token_hash)
            if row is None or row.kind != kind:
                return None
            if time.time() >= row.expires_at:
                await session.execute(
                    delete(McpOAuthToken).where(McpOAuthToken.token_hash == token_hash)
                )
                await session.commit()
                return None
        return row

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        row = await self._load_token_row(refresh_token, "refresh")
        if row is None or row.client_id != client.client_id:
            return None
        return self._refresh_from_row(row, refresh_token)

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        token_hash = _digest(refresh_token.token)
        # リフレッシュ時にも enabled/epoch を再検証する。無効化/失効済みなら新トークンを
        # 発行しない（漏洩リフレッシュトークンや無効化ユーザーの延命を防ぐ）。確認は読み取り
        # だけで先に済ませ、書き込みトランザクションの最初の文を DELETE にする。
        async with self._session() as session:
            peek = await session.get(McpOAuthToken, token_hash)
        if peek is None or peek.kind != "refresh" or peek.client_id != client.client_id:
            raise TokenError("invalid_grant", "Refresh token no longer valid")
        issued_state = (peek.user_id, peek.session_epoch, peek.credential_tag)
        await self._validate_grant_state(peek.subject, issued_state)
        assert client.client_id is not None

        use_scopes = scopes or refresh_token.scopes
        if not set(use_scopes).issubset(refresh_token.scopes):
            raise TokenError("invalid_scope", "Cannot expand refresh token scopes")

        # ローテーション: 旧 refresh を条件付き DELETE ... RETURNING で消費し、新トークンの
        # INSERT まで同一トランザクションで commit する。並行リフレッシュでは片方だけが行を得て、
        # もう片方は invalid_grant。発行側で失敗した場合は旧 refresh も残る（ロールバック）。
        async with self._session() as session:
            result = await session.execute(
                delete(McpOAuthToken)
                .where(
                    McpOAuthToken.token_hash == token_hash,
                    McpOAuthToken.kind == "refresh",
                    McpOAuthToken.client_id == client.client_id,
                    McpOAuthToken.user_id == issued_state[0],
                    McpOAuthToken.session_epoch == issued_state[1],
                    McpOAuthToken.credential_tag == issued_state[2],
                )
                .returning(*McpOAuthToken.__table__.columns)
            )
            row = result.first()
            if (
                row is None
                or self._refresh_from_row(row, refresh_token.token) != refresh_token
                or time.time() >= row.expires_at
            ):
                await session.rollback()
                raise TokenError("invalid_grant", "Refresh token no longer valid")
            access, refresh = await self._issue_tokens(
                session,
                client_id=client.client_id,
                subject=row.subject,
                scopes=use_scopes,
                resource=row.resource,
                issued_state=issued_state,
                grant_id=row.grant_id,
            )
            await session.commit()
        return OAuthToken(
            access_token=access,
            refresh_token=refresh,
            expires_in=_ACCESS_TTL,
            scope=" ".join(use_scopes) if use_scopes else None,
        )

    # -- アクセストークン検証 --

    async def load_access_token(self, token: str) -> AccessToken | None:
        row = await self._load_token_row(token, "access")
        if row is None:
            return None
        # 失効契約（監査 C1）: subject の User が存在し enabled、かつ発行時 epoch/資格情報が
        # 現在値と一致する場合のみ有効。無効化/失効済みならトークンを破棄して拒否する。
        try:
            await self._validate_grant_state(
                row.subject, (row.user_id, row.session_epoch, row.credential_tag)
            )
        except TokenError:
            await self._delete_tokens([row.token_hash])
            return None
        return AccessToken(
            token=token,
            client_id=row.client_id,
            scopes=json.loads(row.scopes),
            expires_at=int(row.expires_at),
            resource=row.resource,
            subject=row.subject,
        )

    async def _validate_grant_state(
        self, subject: str, issued_state: tuple[int, int, str] | None
    ) -> None:
        """発行時 (User.id, session_epoch, 資格情報タグ) が現在値と一致するか検証する。"""
        current = await self._current_user_state(subject)
        if current is None or not current[0] or issued_state is None:
            raise TokenError("invalid_grant", "User authorization no longer valid")
        user_id, epoch, password_hash = current[1]
        if (user_id, epoch) != tuple(issued_state[:2]) or not hmac.compare_digest(
            _credential_tag(password_hash), issued_state[2]
        ):
            raise TokenError("invalid_grant", "User authorization no longer valid")

    async def _delete_tokens(self, token_hashes: Iterable[str]) -> None:
        async with self._session() as session:
            await session.execute(
                delete(McpOAuthToken).where(McpOAuthToken.token_hash.in_(list(token_hashes)))
            )
            await session.commit()

    # -- 失効 --

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        await self._delete_tokens([_digest(token.token)])


class HashedSecretClientAuthenticator(ClientAuthenticator):
    """client_secret を SHA-256 ハッシュで照合する /token 用クライアント認証。

    SDK 標準の ClientAuthenticator は `get_client().client_secret` の平文と比較するが、
    本プロバイダは平文を保存しないため、資格情報の取り出し方は SDK と同一にしたまま
    比較だけをハッシュ同士で行う。
    """

    def __init__(self, provider: MillicallOAuthProvider) -> None:
        super().__init__(provider)
        self._millicall_provider = provider

    async def authenticate_request(self, request: Request) -> OAuthClientInformationFull:
        form_data = await request.form()
        client_id = form_data.get("client_id")
        if not client_id or not isinstance(client_id, str):
            raise AuthenticationError("Missing client_id")

        loaded = await self._millicall_provider._load_client(client_id)
        if loaded is None:
            raise AuthenticationError("Invalid client_id")
        client, secret_hash = loaded

        request_client_secret: str | None = None
        auth_header = request.headers.get("Authorization", "")
        if client.token_endpoint_auth_method == "client_secret_basic":
            if not auth_header.startswith("Basic "):
                raise AuthenticationError(
                    "Missing or invalid Basic authentication in Authorization header"
                )
            try:
                decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
                if ":" not in decoded:
                    raise ValueError("Invalid Basic auth format")
                basic_client_id, request_client_secret = decoded.split(":", 1)
                # RFC 6749 Section 2.3.1: 両方とも URL デコードする（SDK と同一）。
                basic_client_id = unquote(basic_client_id)
                request_client_secret = unquote(request_client_secret)
            except (ValueError, UnicodeDecodeError, binascii.Error):
                raise AuthenticationError("Invalid Basic authentication header") from None
            if basic_client_id != client_id:
                raise AuthenticationError("Client ID mismatch in Basic auth")
        elif client.token_endpoint_auth_method == "client_secret_post":
            raw = form_data.get("client_secret")
            if isinstance(raw, str):
                request_client_secret = raw
        elif client.token_endpoint_auth_method != "none":
            raise AuthenticationError(
                f"Unsupported auth method: {client.token_endpoint_auth_method}"
            )

        # SDK 2.x と同じく、secret 方式で登録されたのに保存済み secret が無いクライアントは
        # 何も検証していないので通さない。
        if client.token_endpoint_auth_method != "none" and secret_hash is None:
            raise AuthenticationError(
                "Client is registered for secret-based authentication but has no stored secret"
            )
        if secret_hash is not None:
            if not request_client_secret:
                raise AuthenticationError("Client secret is required")
            if not hmac.compare_digest(secret_hash, _digest(request_client_secret)):
                raise AuthenticationError("Invalid client_secret")
            if client.client_secret_expires_at and client.client_secret_expires_at < int(
                time.time()
            ):
                raise AuthenticationError("Client secret has expired")
        return client


def install_hashed_client_auth(
    routes: list[BaseRoute], provider: MillicallOAuthProvider
) -> list[BaseRoute]:
    """SDK が生成した /token ルートを HashedSecretClientAuthenticator 版に差し替える。

    構成は mcp SDK 2.x の create_auth_routes と同じ（`cors_middleware(TokenHandler(...).handle)`）。
    SDK 2.x は /token に本文サイズ上限を掛けなくなったが、1.x と同じ 4 MiB 上限を内側に残す
    （差し替えで防御を弱めない）。identity assertion（jwt-bearer グラント）は本プロバイダが
    実装しないため無効のまま。
    /token が見つからなければ起動を失敗させる（SDK 更新で差し替えが黙って外れるのを防ぐ）。
    /revoke は無効（RevocationOptions 未指定）。有効化された場合は SDK 標準認証のままに
    なってしまうため、存在したら起動を止める（差し替え対象に加えること）。
    """
    if any(isinstance(route, Route) and route.path == "/revoke" for route in routes):
        raise RuntimeError("MCP OAuth /revoke route requires hashed client auth; not supported")
    handler = TokenHandler(
        provider, HashedSecretClientAuthenticator(provider), identity_assertion_enabled=False
    )
    cors_app = cors_middleware(handler.handle, ["POST", "OPTIONS"])
    # cors_middleware は CORSMiddleware(app=request_response(handler)) を返す。内側に本文上限を挟む。
    cors_app.app = RequestBodyLimitMiddleware(cors_app.app, DEFAULT_MAX_REQUEST_BODY_SIZE)
    replacement = Route("/token", endpoint=cors_app, methods=["POST", "OPTIONS"])
    replaced = 0
    result: list[BaseRoute] = []
    for route in routes:
        if isinstance(route, Route) and route.path == "/token":
            result.append(replacement)
            replaced += 1
        else:
            result.append(route)
    if replaced != 1:
        raise RuntimeError("MCP OAuth /token route not found; cannot install client auth")
    return result


class ResourceBoundTokenVerifier(TokenVerifier):
    """resource（RFC 8707）が本サーバー（issuer）宛のアクセストークンだけを受理する検証器。

    mcp SDK 1.x の `AuthSettings.validate_token_resource=True`（BearerAuthBackend 内の
    `_issued_for_this_resource`）は 2.x で廃止されたため、同じ比較規則（URL として正規化し、
    末尾スラッシュ差を無視）をここで再実装する。provider は `load_access_token` で
    期限・ユーザー有効性・epoch を検証済みのトークンを返す。
    """

    def __init__(self, provider: MillicallOAuthProvider, resource_server_url: str) -> None:
        self._provider = provider
        self._resource = str(AnyHttpUrl(resource_server_url)).removesuffix("/")

    async def verify_token(self, token: str) -> AccessToken | None:
        access = await self._provider.load_access_token(token)
        if access is None:
            return None
        try:
            token_resource = str(AnyHttpUrl(access.resource or ""))
        except ValidationError:
            return None
        if token_resource.removesuffix("/") != self._resource:
            logger.warning("MCP OAuth: rejected bearer token for another resource")
            return None
        return access
