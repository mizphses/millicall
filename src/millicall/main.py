import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from millicall.ai_agents.router import router as ai_agents_router
from millicall.auth.router import router as auth_router
from millicall.auth.service import ensure_admin_user
from millicall.calls.router import router as calls_router
from millicall.cdr.router import router as cdr_router
from millicall.config import Settings, get_settings
from millicall.contacts.router import router as contacts_router
from millicall.db import create_db_engine
from millicall.db_migrations import upgrade_to_head
from millicall.extensions.router import router as extensions_router
from millicall.media.audio_fork import MediaEventRouter, register_media_ws
from millicall.media.service import SessionRegistry
from millicall.providers.router import router as providers_router
from millicall.routes_config.router import router as routes_router
from millicall.secrets_store import load_or_create_secrets
from millicall.telephony.esl import ESLClient
from millicall.telephony.events import CdrRecorder, EslEventListener
from millicall.telephony.service import (
    TelephonyChangeListener,
    build_config_writer,
    build_esl_factory,
)
from millicall.trunks.router import router as trunks_router

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
    media_router = MediaEventRouter(app.state.session_registry)

    # AI 再生制御用の共有 ESL コマンドクライアント（発着信制御と別接続）。
    # ESL 未到達（接続拒否・ハング）でも起動を止めない — timeout 付きで試行し warning のみ。
    esl_command = esl_factory()
    try:
        await asyncio.wait_for(esl_command.connect(), timeout=settings.esl_timeout_seconds)
    except Exception:  # noqa: BLE001
        logger.warning("ESL command client connect failed; AI playback disabled until reconnect")
    app.state.esl_command = esl_command

    async def _compose_handler(event: dict) -> None:
        await recorder.handle(event)
        await media_router.handle(event)

    def _make_event_client(handler):
        return ESLClient(
            settings.esl_host, settings.esl_port, app.state.secrets.esl_password, on_event=handler
        )

    event_listener = EslEventListener(
        _make_event_client,
        ["CHANNEL_CREATE", "CHANNEL_ANSWER", "CHANNEL_HANGUP_COMPLETE", "PLAYBACK_STOP"],
        _compose_handler,
    )
    await event_listener.start()
    app.state.event_listener = event_listener

    try:
        yield
    finally:
        await event_listener.stop()
        await esl_command.close()
        await engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="millicall v2 core", lifespan=lifespan)
    app.state.settings = settings or get_settings()
    app.include_router(auth_router)
    app.include_router(contacts_router)
    app.include_router(extensions_router)
    app.include_router(trunks_router)
    app.include_router(routes_router)
    app.include_router(cdr_router)
    app.include_router(calls_router)
    app.include_router(providers_router)
    app.include_router(ai_agents_router)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    register_media_ws(app)
    return app


app = create_app()
