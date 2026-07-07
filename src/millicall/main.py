import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from millicall.ai_agents.router import router as ai_agents_router
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
from millicall.routes_config.router import router as routes_router
from millicall.scim.router import api_router as scim_api_router
from millicall.scim.router import scim_router
from millicall.secrets_store import load_or_create_secrets
from millicall.system.router import router as system_router
from millicall.users.router import router as users_router
from millicall.telephony.esl import ESLClient
from millicall.telephony.events import CdrRecorder, EslEventListener
from millicall.telephony.service import (
    TelephonyChangeListener,
    build_config_writer,
    build_esl_factory,
)
from millicall.trunks.router import router as trunks_router
from millicall.tts_cache.router import router as tts_cache_router
from millicall.workflows.errors import WorkflowValidationError
from millicall.workflows.router import router as workflows_router
from millicall.workflows.runner import WorkflowRunner
from millicall.workflows.service import NoLlmProviderError

logger = logging.getLogger("millicall")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = app.state.settings
    settings.data_dir.mkdir(parents=True, exist_ok=True)

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
    writer = build_config_writer(settings, app.state.secrets)
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

    async with app.state.sessionmaker() as session:
        await listener.regenerate(session)

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
    {"api", "media", "healthz", "openapi.json", "docs", "redoc", "mcp", ".well-known", "provisioning", "scim"}
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
    # CSRF 保護ミドルウェア（double-submit cookie パターン）。
    # ルーター登録より前に追加することで全ルートに適用される。
    app.add_middleware(CsrfMiddleware)
    app.include_router(auth_router)
    app.include_router(saml_router)
    app.include_router(totp_router)
    app.include_router(audit_router)
    app.include_router(contacts_router)
    app.include_router(extensions_router)
    app.include_router(trunks_router)
    app.include_router(routes_router)
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
