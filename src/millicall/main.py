import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from millicall.ai_agents.router import router as ai_agents_router
from millicall.app_settings.router import router as app_settings_router
from millicall.app_settings.service import SettingsService
from millicall.audit_router import router as audit_router
from millicall.auth.csrf import CsrfMiddleware
from millicall.auth.router import router as auth_router
from millicall.auth.saml.router import router as saml_router
from millicall.auth.service import ensure_admin_user
from millicall.auth.totp_router import router as totp_router
from millicall.call_messages.router import router as call_messages_router
from millicall.calls.router import router as calls_router
from millicall.cdr.router import router as cdr_router
from millicall.config import Settings, get_settings
from millicall.contacts.router import router as contacts_router
from millicall.db import create_db_engine
from millicall.db_migrations import upgrade_to_head
from millicall.extensions.router import router as extensions_router
from millicall.mcp_server.ephemeral import EphemeralAgentStore
from millicall.mcp_server.integration import mcp_session_context, mount_mcp
from millicall.media.audio_fork import MediaEventRouter, register_media_ws
from millicall.media.dtmf import DtmfCollector
from millicall.media.service import AnswerRegistry, HangupRegistry, SessionRegistry
from millicall.network.client import NetdClient
from millicall.network.router import router as network_router
from millicall.providers.router import router as providers_router
from millicall.provisioning.devices_router import router as devices_router
from millicall.provisioning.router import router as provisioning_router
from millicall.ring_groups.router import router as ring_groups_router
from millicall.scim.router import api_router as scim_api_router
from millicall.scim.router import scim_router
from millicall.secrets_store import load_or_create_secrets
from millicall.system.router import router as system_router
from millicall.telephony.esl import ESLClient
from millicall.telephony.events import CdrRecorder, EslEventListener
from millicall.telephony.service import (
    TelephonyChangeListener,
    build_config_writer,
    build_esl_factory,
)
from millicall.trunks.router import router as trunks_router
from millicall.tts_cache.router import router as tts_cache_router
from millicall.users.router import router as users_router
from millicall.workflows.errors import WorkflowValidationError
from millicall.workflows.router import router as workflows_router
from millicall.workflows.runner import WorkflowRunner
from millicall.workflows.service import NoLlmProviderError

logger = logging.getLogger("millicall")

# CSP ポリシー選定の根拠:
#   - SPA は Vite ビルド済みバンドル（/assets/*.js, /assets/*.css）のみを使用し、
#     インラインスクリプトは一切含まない（frontend/dist/index.html で確認済み）。
#     そのため script-src に 'unsafe-inline' は不要。
#   - PandaCSS は CSS ファイルとして出力されるため style-src は 'self' で足りるが、
#     ランタイムに css-in-js 風の style 属性を使うコンポーネントがある可能性を考慮し
#     'unsafe-inline' を style-src に限り許容する（スクリプト実行ではなく見た目のみ）。
#   - img-src に data: を許可するのは UI でのアバター/QR コード等の data URI 表示のため。
#   - HSTS は設定しない。TLS は front（nginx 等）が担当し、
#     core は HTTP で動作するため plain-HTTP origin から HSTS を発行するのは誤り。
_CSP_POLICY = (
    "default-src 'self'; "
    "img-src 'self' data:; "
    "style-src 'self' 'unsafe-inline'; "
    "script-src 'self'; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """全レスポンスにセキュリティヘッダーを付与するミドルウェア。

    ヘッダーは additive（既存レスポンスに追加）なので、SAML/MCP/provisioning
    レスポンスの機能には影響しない。
    HSTS は設定しない（TLS は front 側の責務）。
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = _CSP_POLICY
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response


async def _reapply_network_config_on_startup(app: FastAPI, settings: Settings) -> None:
    """core 起動時に保存済みネットワーク設定を netd へ best-effort で再適用する。

    netd はステートレスのため、インターフェース IP・nftables・dnsmasq 設定は
    OS/コンテナ再起動で失われる。DB の NetworkConfig を読み、過去に一度でも
    apply に成功している（applied=True）場合のみ netd へ apply_dhcp / apply_nat を
    再送してホストのランタイム状態を復旧する。

    設定が無い・applied=False の場合は何もしない（デフォルト設定のまま勝手に
    インターフェースを掴むのを防ぐ）。netd 未起動・接続失敗・その他例外はすべて
    warning ログのみで握りつぶし、例外を送出しない（core 起動を止めないため）。
    """
    from millicall.models import NetworkConfig
    from millicall.network.apply import apply_network_config_to_netd

    try:
        async with app.state.sessionmaker() as session:
            cfg = await session.get(NetworkConfig, 1)
        if cfg is None or not cfg.applied:
            # 未保存 or 未適用（デフォルトのまま）なら起動時再適用は行わない。
            return
        await apply_network_config_to_netd(cfg, app.state.netd_client, settings)
        logger.info("起動時にネットワーク設定を netd へ再適用しました")
    except Exception:  # noqa: BLE001 — netd 未起動/接続失敗/NetdError を含めて起動は止めない
        logger.warning(
            "起動時のネットワーク設定再適用に失敗しました（core 起動は継続します）",
            exc_info=True,
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = app.state.settings
    settings.data_dir.mkdir(parents=True, exist_ok=True)

    # alembic の fileConfig(disable_existing_loggers=True) によって millicall.* ロガーが
    # 無効化されることがある。upgrade_to_head 実行前に ERROR レベルを明示的に保証する。
    # basicConfig は呼ばず、既存のハンドラ（RichHandler 等）を破壊しない。
    _mc_logger = logging.getLogger("millicall")
    if _mc_logger.level == logging.NOTSET or _mc_logger.disabled:
        _mc_logger.setLevel(logging.INFO)
    _mc_logger.disabled = False

    if not settings.cookie_secure:
        logger.warning(
            "cookie_secure=False — HTTPS外でセッションCookieが平文送信されます（LAN内運用前提）"
        )

    # upgrade_to_head は同期関数のため asyncio.to_thread で実行してイベントループをブロックしない
    await asyncio.to_thread(upgrade_to_head, settings.database_url)
    app.state.secrets = load_or_create_secrets(settings.data_dir)

    # MCP OAuth プロバイダ（mount_mcp で create_app 時に生成済み）へ、認可パラメータ
    # 署名用の SecretBox を注入する。secrets はここで初めて確定するため lifespan で行う。
    _mcp_provider = getattr(app.state, "mcp_oauth_provider", None)
    if _mcp_provider is not None:
        from millicall.crypto import SecretBox

        _mcp_provider.set_signer(SecretBox(app.state.secrets.master_key))

    engine = create_db_engine(settings.database_url)
    app.state.engine = engine
    app.state.sessionmaker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    # MCP OAuth の Bearer 面にも Cookie 面と同等の失効契約（enabled/session_epoch 照合）を
    # 持たせるため sessionmaker を注入する（監査 C1）。
    if _mcp_provider is not None:
        _mcp_provider.set_sessionmaker(app.state.sessionmaker)

    # 管理画面から編集可能な設定のマージ層（env デフォルト + app_settings 上書き）。
    # 以降の起動処理・各ルーターは settings_service.effective() 経由で実効設定を読む。
    from millicall.crypto import SecretBox

    app.state.settings_service = SettingsService(
        app.state.sessionmaker, settings, SecretBox(app.state.secrets.master_key)
    )
    # FreeSWITCH 設定（国際発信 allowlist / 匿名着信拒否）は DB 上書きを含む実効設定で生成する。
    effective = await app.state.settings_service.effective()
    writer = build_config_writer(effective, app.state.secrets)
    esl_factory = build_esl_factory(settings, app.state.secrets)
    app.state.esl_factory = esl_factory
    listener = TelephonyChangeListener(
        writer, esl_factory, esl_timeout=settings.esl_timeout_seconds
    )
    app.state.change_listener = listener

    async with app.state.sessionmaker() as session:
        new_admin_password = await ensure_admin_user(session)
    if new_admin_password:
        print(
            "[millicall] 初期管理者を作成しました -> "
            f"username=admin password={new_admin_password}  "
            "(この表示は一度きりです。安全に保管してください)"
        )

    # 設定を再生成し、reloadxml まで送る（notify）。core のみ更新・再起動される
    # ケースでは FreeSWITCH が動き続けているため、reloadxml を送らないと
    # テンプレート変更が反映されずキャッシュ済み dialplan が古いまま残る。
    # FreeSWITCH 未起動（初回同時起動）なら notify 内で警告ログのみ出して継続する。
    async with app.state.sessionmaker() as session:
        await listener.notify(session)

    recorder = CdrRecorder(app.state.sessionmaker)

    settings.tts_cache_dir.mkdir(parents=True, exist_ok=True)
    app.state.session_registry = SessionRegistry()
    # 発信オーケストレーション（MCP dial/converse）の応答待ちレジストリ。
    # MediaEventRouter が CHANNEL_ANSWER で解決する。
    app.state.answer_registry = AnswerRegistry()
    # converse（Task 4）用: 通話終了完了待ちレジストリと一時エージェントストア。
    # MediaEventRouter が CHANNEL_HANGUP_COMPLETE で hangup を解決し、audio_fork_ws が
    # ?agent=ephemeral のとき ephemeral_store を call_uuid で引いてセッションを組む。
    app.state.hangup_registry = HangupRegistry()
    app.state.ephemeral_store = EphemeralAgentStore()
    app.state.dtmf_collector = DtmfCollector()

    # netd UNIX ソケットクライアント（接続は遅延生成 — 呼び出し時に毎回接続する）。
    # netd が起動していない開発・テスト環境でも app 起動は止めない。
    app.state.netd_client = NetdClient(settings.netd_socket_path)

    # OS/コンテナ再起動後のネットワーク設定自動再適用（best-effort）。
    # netd はステートレスなため、インターフェース IP・nftables・dnsmasq 設定は
    # 再起動で消える。保存済み設定を起動時に netd へ再適用して子ネットワークを復旧する。
    # applied=True（過去に手動 apply 済み）のときだけ再適用し、一度も適用していない
    # デフォルト環境で勝手に enp3s0 を掴むのを防ぐ。netd 未起動・接続失敗・NetdError は
    # warning ログのみで握りつぶし、core 起動は止めない（telephony の reloadxml と同じ思想）。
    await _reapply_network_config_on_startup(app, settings)

    # AI 再生制御用の共有 ESL コマンドクライアント（発着信制御と別接続）。
    # ESL 未到達（接続拒否・ハング）でも起動を止めない — timeout 付きで試行し warning のみ。
    esl_command = esl_factory()
    try:
        await asyncio.wait_for(esl_command.connect(), timeout=settings.esl_timeout_seconds)
    except Exception:  # noqa: BLE001
        logger.warning("ESL command client connect failed; AI playback disabled until reconnect")
    app.state.esl_command = esl_command

    # 共有 ESL コマンド接続の書き込みを直列化するロック（I6: 複数通話の bgapi 混線防止）。
    app.state.esl_command_lock = asyncio.Lock()

    async def _esl_reconnect() -> object:
        """接続断時に新規 ESLClient を生成・接続して app.state.esl_command を差し替える。
        再接続後の新接続を返すことで、既存の EslCallControl も self._esl を更新できる。
        """
        new_esl = esl_factory()
        try:
            await asyncio.wait_for(new_esl.connect(), timeout=settings.esl_timeout_seconds)
        except Exception:  # noqa: BLE001
            logger.warning("ESL command reconnect failed; AI playback remains disabled")
            raise
        app.state.esl_command = new_esl
        return new_esl

    app.state.esl_reconnect = _esl_reconnect

    app.state.workflow_runner = WorkflowRunner(
        sessionmaker=app.state.sessionmaker,
        secrets=app.state.secrets,
        esl=esl_command,
        esl_lock=app.state.esl_command_lock,
        esl_reconnect=_esl_reconnect,
        session_registry=app.state.session_registry,
        settings=settings,
        dtmf_collector=app.state.dtmf_collector,
        settings_service=app.state.settings_service,
    )

    # 着信 AI 応対では CHANNEL_ANSWER を受けた core が uuid_audio_stream を発行するため、
    # 共有 ESL コマンド接続・lock・reconnect と core への WS ベース URL を注入する。
    media_router = MediaEventRouter(
        app.state.session_registry,
        esl=esl_command,
        ws_base_url=settings.media_ws_base_url,
        lock=app.state.esl_command_lock,
        reconnect=_esl_reconnect,
        answer_registry=app.state.answer_registry,
        hangup_registry=app.state.hangup_registry,
        dtmf_collector=app.state.dtmf_collector,
        workflow_runner=app.state.workflow_runner,
    )

    async def _compose_handler(event: dict) -> None:
        await recorder.handle(event)
        await media_router.handle(event)

    def _make_event_client(handler):
        return ESLClient(
            settings.esl_host, settings.esl_port, app.state.secrets.esl_password, on_event=handler
        )

    event_listener = EslEventListener(
        _make_event_client,
        ["CHANNEL_CREATE", "CHANNEL_ANSWER", "CHANNEL_HANGUP_COMPLETE", "PLAYBACK_STOP", "DTMF"],
        _compose_handler,
    )
    await event_listener.start()
    app.state.event_listener = event_listener

    # MCP StreamableHTTP session manager を起動/停止する（mount_mcp 済みのときのみ実体を持つ）。
    async with mcp_session_context(app):
        try:
            yield
        finally:
            await event_listener.stop()
            await esl_command.close()
            await engine.dispose()


# SPA catch-all が index.html を返してはいけないパス接頭辞（API/メディア/ヘルス/ドキュメント）。
# これらに該当する未定義 GET は 404 を返し、API のセマンティクスを保つ。
_SPA_EXCLUDED_PREFIXES = frozenset(
    {
        "api",
        "media",
        "healthz",
        "openapi.json",
        "docs",
        "redoc",
        "mcp",
        ".well-known",
        "provisioning",
        "scim",
    }
)


def _mount_spa(app: FastAPI, static_dir: Path) -> None:
    """ビルド済み SPA を配信する。/assets はハッシュ付きアセット、それ以外の GET は
    index.html にフォールバックする（クライアントサイドルーティング）。

    catch-all は既存の API/WS ルート登録より後に呼ぶこと。ルートは登録順に評価されるため、
    先に登録済みの /api・/media（WS 含む）・/healthz 等が優先され、catch-all に食われない。
    """
    index_file = static_dir / "index.html"
    assets_dir = static_dir / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str) -> FileResponse:
        first_segment = full_path.split("/", 1)[0]
        if first_segment in _SPA_EXCLUDED_PREFIXES:
            raise HTTPException(status_code=404)
        return FileResponse(index_file)


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="millicall v2 core", lifespan=lifespan)
    app.state.settings = settings or get_settings()
    # セキュリティヘッダーミドルウェア（最外層）。
    # CSP / X-Content-Type-Options / X-Frame-Options / Referrer-Policy を全レスポンスに付与。
    # HSTS は設定しない（TLS は front の責務; core は HTTP で動作）。
    app.add_middleware(SecurityHeadersMiddleware)
    # CSRF 保護ミドルウェア（double-submit cookie パターン）。
    # ルーター登録より前に追加することで全ルートに適用される。
    app.add_middleware(CsrfMiddleware)
    app.include_router(auth_router)
    app.include_router(app_settings_router)
    app.include_router(saml_router)
    app.include_router(totp_router)
    app.include_router(audit_router)
    app.include_router(contacts_router)
    app.include_router(extensions_router)
    app.include_router(trunks_router)
    app.include_router(ring_groups_router)
    app.include_router(cdr_router)
    app.include_router(call_messages_router)
    app.include_router(calls_router)
    app.include_router(providers_router)
    app.include_router(ai_agents_router)
    app.include_router(tts_cache_router)
    app.include_router(workflows_router)
    app.include_router(network_router)
    app.include_router(provisioning_router)
    app.include_router(devices_router)
    app.include_router(scim_api_router)
    app.include_router(scim_router)
    app.include_router(system_router)
    app.include_router(users_router)

    @app.exception_handler(WorkflowValidationError)
    async def _workflow_validation_handler(_request, exc: WorkflowValidationError):
        # 壊れた定義の保存拒否（旧の最重要問題を解消）: ハード違反は 422。
        return JSONResponse(status_code=422, content={"detail": exc.errors})

    @app.exception_handler(NoLlmProviderError)
    async def _no_llm_handler(_request, exc: NoLlmProviderError):
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    register_media_ws(app)

    # MCP（/mcp + OAuth 2.1 + /mcp-login）を SPA catch-all より前に取り込む。
    mount_mcp(app)

    # SPA は最後にマウントする（catch-all を既存ルートの後段に置くため）。
    # static_dir が存在しない開発時は無効（Vite dev server + proxy を使う）。
    static_dir = app.state.settings.static_dir
    if (static_dir / "index.html").is_file():
        _mount_spa(app, static_dir)

    return app


app = create_app()
