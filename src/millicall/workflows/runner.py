"""ワークフロー着信ランナー (Phase 4b Task 9).

WorkflowRunner はチャネルが着信・応答・パーク済みの状態で呼ばれ、
指定されたワークフロー定義を ChannelContext 上で実行する。
依存関係はすべてコンストラクタで注入し、app.state には触れない。
"""

import asyncio
import json
import logging

from sqlalchemy import select

import millicall.workflows.handlers  # noqa: F401 — ハンドラをグローバルレジストリに登録
from millicall.ai import registry as ai_registry
from millicall.crypto import SecretBox
from millicall.mcp_server.primitives import CallPrimitives
from millicall.media.call_control import EslCallControl
from millicall.media.service import SessionRegistry
from millicall.models import AiAgent, Provider, Workflow
from millicall.workflows.context import ChannelContext
from millicall.workflows.email_sender import SmtpEmailSender
from millicall.workflows.executor import WorkflowExecutor
from millicall.workflows.schema import WorkflowDefinition

logger = logging.getLogger("millicall.workflows.runner")


def _build_provider_from_row(p: Provider, key: str | None):
    """Provider ORM 行から適切なプロバイダオブジェクトを構築する。"""
    config = json.loads(p.config_json or "{}")
    if p.type == "llm":
        return ai_registry.build_llm(p.kind, config, key)
    if p.type == "tts":
        return ai_registry.build_tts(p.kind, config, key)
    if p.type == "stt":
        return ai_registry.build_stt(p.kind, config, key)
    raise ValueError(f"unknown provider type: {p.type!r}")


class WorkflowRunner:
    """着信チャネル上でワークフローを実行するランナー。

    MediaEventRouter が CHANNEL_ANSWER で variable_millicall_workflow を検出した際に
    asyncio.create_task() で呼び出される。EslCallControl の構築から
    ChannelContext の組み立て、WorkflowExecutor の実行、後片付けまでを担う。
    """

    def __init__(
        self,
        *,
        sessionmaker,
        secrets,
        esl,
        esl_lock: asyncio.Lock,
        esl_reconnect,
        session_registry: SessionRegistry,
        settings,
        dtmf_collector,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._secrets = secrets
        self._esl = esl
        self._lock = esl_lock
        self._reconnect = esl_reconnect
        self._session_registry = session_registry
        self._settings = settings
        self._dtmf_collector = dtmf_collector

    async def start(self, uuid: str, workflow_id: int) -> None:
        """ワークフローを起動する。

        パーク済みチャネル uuid 上で workflow_id のワークフローを実行する。
        ワークフローが見つからない / 無効の場合はチャネルをハングアップして返る。
        ハンドラ例外が発生しても finally で必ずハングアップ・後片付けを行う。
        """
        # --- ワークフロー読み込み ------------------------------------------- #
        workflow: Workflow | None = None
        try:
            async with self._sessionmaker() as db:
                workflow = await db.get(Workflow, workflow_id)
        except Exception:
            logger.exception("workflow runner: DB lookup failed for workflow_id=%d", workflow_id)

        if workflow is None or not workflow.enabled:
            logger.warning(
                "workflow runner: workflow %d not found or disabled; hanging up uuid=%s",
                workflow_id,
                uuid,
            )
            await self._safe_hangup(uuid)
            return

        # --- プロバイダ構築 -------------------------------------------------- #
        box = SecretBox(self._secrets.master_key)
        tts = None
        stt = None

        try:
            tts, stt = await self._build_tts_stt(workflow, box)
        except Exception:
            logger.exception("workflow runner: provider build failed for workflow_id=%d", workflow_id)

        if tts is None or stt is None:
            logger.warning(
                "workflow runner: could not build primitives (tts=%s, stt=%s) "
                "for workflow_id=%d; call_control will still work",
                tts, stt, workflow_id,
            )

        # --- EslCallControl + CallPrimitives -------------------------------- #
        call_control = EslCallControl(
            self._esl, uuid, lock=self._lock, reconnect=self._reconnect
        )

        primitives: CallPrimitives | None = None
        if tts is not None and stt is not None:
            primitives = CallPrimitives(
                esl=self._esl,
                call_uuid=uuid,
                call_control=call_control,
                tts=tts,
                stt=stt,
                tts_dir=self._settings.tts_cache_dir,
                lock=self._lock,
                reconnect=self._reconnect,
            )

        # --- レジストリ登録 ------------------------------------------------- #
        # PLAYBACK_STOP が call_control._notify_playback_done() に届くよう登録する。
        # session は None でも SessionRegistry は受け入れる（dict に格納するのみ）。
        self._session_registry.register(uuid, None, call_control)
        self._dtmf_collector.register(uuid)

        # ChannelContext 構築前に例外が発生した場合も finally で後片付けできるよう sentinel を置く。
        ctx = None
        try:
            # --- ChannelContext 組み立て ---------------------------------------- #
            ctx = ChannelContext(
                uuid=uuid,
                call_control=call_control,
                primitives=primitives,
                tts_dir=self._settings.tts_cache_dir,
                sessionmaker=self._sessionmaker,
                secrets=self._secrets,
                esl=self._esl,
                dtmf=self._dtmf_collector.bind(uuid),
                provider_resolver=self._make_provider_resolver(box),
                agent_resolver=self._make_agent_resolver(),
                default_tts_provider_id=workflow.default_tts_provider_id,
                smtp=SmtpEmailSender.from_settings(self._settings),
                # 最上位ワークフロー id を呼び出しスタックに入れ、call_workflow の
                # cross-workflow 循環（A→B→A）を実行時に検出できるようにする。
                active_workflow_ids={workflow_id},
            )

            # --- 実行 ----------------------------------------------------------- #
            defn = WorkflowDefinition.model_validate(json.loads(workflow.definition_json))
            await WorkflowExecutor(defn, ctx).execute()
        except Exception:
            logger.exception(
                "workflow runner: execution error for workflow_id=%d uuid=%s",
                workflow_id, uuid,
            )
        finally:
            # ハングアップが済んでいなければ切断する（正常終了・例外どちらでも）。
            # ctx が None の場合は ChannelContext 構築前に例外が発生したケース。
            if not (ctx is not None and ctx.hung_up):
                if ctx is not None:
                    await self._safe_hangup_ctx(ctx)
                else:
                    await self._safe_hangup(uuid)
            self._session_registry.pop(uuid)
            self._dtmf_collector.unregister(uuid)

    # ------------------------------------------------------------------ #
    # 内部ヘルパー
    # ------------------------------------------------------------------ #

    async def _build_tts_stt(self, workflow: Workflow, box: SecretBox):
        """TTS / STT プロバイダを構築して返す。失敗時は None を返す（例外を伝播しない）。"""
        tts = None
        stt = None
        async with self._sessionmaker() as db:
            # TTS: workflow.default_tts_provider_id があればそれを使い、
            # なければ最初の有効 TTS プロバイダにフォールバック。
            tts_pid = workflow.default_tts_provider_id
            if tts_pid is not None:
                p = await db.get(Provider, tts_pid)
                if p is not None and p.enabled:
                    try:
                        key = box.decrypt(p.api_key_encrypted) if p.api_key_encrypted else None
                        tts = _build_provider_from_row(p, key)
                    except Exception:
                        logger.warning("workflow runner: TTS provider %d build failed", tts_pid, exc_info=True)
            if tts is None:
                p = await db.scalar(
                    select(Provider).where(Provider.type == "tts", Provider.enabled.is_(True))
                )
                if p is not None:
                    try:
                        key = box.decrypt(p.api_key_encrypted) if p.api_key_encrypted else None
                        tts = _build_provider_from_row(p, key)
                    except Exception:
                        logger.warning("workflow runner: fallback TTS build failed", exc_info=True)

            # STT: 最初の有効 STT プロバイダ。
            p_stt = await db.scalar(
                select(Provider).where(Provider.type == "stt", Provider.enabled.is_(True))
            )
            if p_stt is not None:
                try:
                    key = box.decrypt(p_stt.api_key_encrypted) if p_stt.api_key_encrypted else None
                    stt = _build_provider_from_row(p_stt, key)
                except Exception:
                    logger.warning("workflow runner: STT provider build failed", exc_info=True)

        return tts, stt

    def _make_provider_resolver(self, box: SecretBox):
        """provider_id -> built provider のクロージャを返す。"""
        sessionmaker = self._sessionmaker

        async def _resolver(pid: int):
            async with sessionmaker() as db:
                try:
                    p = await db.get(Provider, pid)
                    if p is None:
                        return None
                    key = box.decrypt(p.api_key_encrypted) if p.api_key_encrypted else None
                    return _build_provider_from_row(p, key)
                except Exception:
                    logger.warning("provider_resolver(%d) failed", pid, exc_info=True)
                    return None

        return _resolver

    def _make_agent_resolver(self):
        """agent_id -> AiAgent | None のクロージャを返す。"""
        sessionmaker = self._sessionmaker

        async def _resolver(agent_id: int):
            async with sessionmaker() as db:
                return await db.get(AiAgent, agent_id)

        return _resolver

    async def _safe_hangup(self, uuid: str) -> None:
        """例外を吸収してチャネルをハングアップする（call_control 構築前用）。"""
        try:
            cc = EslCallControl(self._esl, uuid, lock=self._lock, reconnect=self._reconnect)
            await cc.hangup()
        except Exception:
            logger.warning("workflow runner: hangup failed for uuid=%s", uuid, exc_info=True)

    async def _safe_hangup_ctx(self, ctx: ChannelContext) -> None:
        """例外を吸収して ctx.hangup() を呼ぶ（実行後片付け用）。"""
        try:
            await ctx.hangup()
        except Exception:
            logger.warning("workflow runner: final hangup failed for uuid=%s", ctx.uuid, exc_info=True)
