"""mod_audio_stream の WS を受けて VAD→会話へ流すハンドラと、FreeSWITCH イベントを
media セッションへ振り分けるルータ、および WS ルート登録。

FreeSWITCH 側は ESL API `uuid_audio_stream <uuid> start <ws-url> mono 8k` で
mod_audio_stream を起動し、`ws://.../media/audio-fork/{call_uuid}?agent={id}` へ
L16/8k モノのバイナリフレームを送出する（`uuid_audio_stream <uuid> stop` で停止）。
本モジュールはその WS 受け側（バイナリ L16 フレーム受信）を担う。
"""

import asyncio
import contextlib
import logging

from fastapi import FastAPI, WebSocket
from starlette.websockets import WebSocketDisconnect

from millicall.app_settings.service import effective_settings
from millicall.media.dtmf import DtmfCollector
from millicall.media.service import (
    AnswerRegistry,
    HangupRegistry,
    SessionRegistry,
    build_conversation_session,
    build_conversation_session_from_spec,
)
from millicall.media.vad import VadSegmenter
from millicall.telephony.esl import ESLConnectionClosed

logger = logging.getLogger("millicall.media.audio_fork")


class AudioForkHandler:
    def __init__(self, session, segmenter: VadSegmenter) -> None:
        self._session = session
        self._segmenter = segmenter
        self._current: asyncio.Task | None = None
        self._bg: set[asyncio.Task] = set()

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)
        task.add_done_callback(self._log_task_error)
        return task

    def _log_task_error(self, task: asyncio.Task) -> None:
        """タスクが CancelledError 以外の例外で終了した場合に ERROR ログを出す。

        CancelledError は WS 切断時のキャンセル（正常系）なのでログしない。
        STT の RuntimeError（依存未同梱）、LLM の HTTP 400 など、
        本来は切り分けが必要な例外を必ず stdout に出す。
        """
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error(
                "on_utterance/on_barge_in タスクが例外で終了しました (uuid=%s)",
                getattr(self._session, "_call_uuid", "unknown"),
                exc_info=exc,
            )

    async def run(self, ws) -> None:
        uuid = getattr(self._session, "_call_uuid", "unknown")
        frames = 0
        speech_ends = 0
        # ストリーミング STT: speech_start で STT ストリームを開き、発話中の各フレームを
        # 逐次 feed、speech_end で finalize する。発話中に文字化が進むため、発話終端での
        # STT 確定がほぼ即座になり、STT レイテンシがクリティカルパスから外れる。
        in_utterance = False
        try:
            while True:
                frame = await ws.receive_bytes()
                frames += 1
                if frames == 1:
                    logger.info(
                        "audio-fork: 最初の音声フレーム受信 (uuid=%s, bytes=%d)", uuid, len(frame)
                    )
                for ev in self._segmenter.push(frame):
                    if ev.kind == "speech_start":
                        # 発話開始で STT ストリームを開く（再生中でも開いて割り込み発話を取りこぼさない）。
                        self._session.open_stt_stream()
                        in_utterance = True
                        if self._session.speaking:
                            self._spawn(self._session.on_barge_in())
                    elif ev.kind == "speech_end":
                        if not in_utterance:
                            continue
                        in_utterance = False
                        speech_ends += 1
                        logger.info(
                            "audio-fork: VAD speech_end (uuid=%s, audio_bytes=%d)",
                            uuid,
                            len(ev.audio),
                        )
                        # 十分な長さ かつ 直前の応答が完了済みのときだけ応答生成へ。
                        # audio="" は VAD が短すぎと判定した発話 → 破棄。
                        # 直前応答が処理中なら取りこぼし防止のため破棄（バージインで解放される）。
                        if ev.audio and (self._current is None or self._current.done()):
                            self._current = self._spawn(self._session.finalize_stt_stream())
                        else:
                            self._spawn(self._session.abort_stt_stream())
                # 発話中は受信フレームを STT ストリームへ逐次投入する（真のストリーミング）。
                if in_utterance:
                    await self._session.feed_stt_stream(frame)
        except WebSocketDisconnect:
            return
        finally:
            logger.info(
                "audio-fork: 受信ループ終了 (uuid=%s, frames=%d, speech_end=%d)",
                uuid,
                frames,
                speech_ends,
            )
            # 発話中に切断された場合、開きっぱなしの STT ストリームを閉じる。
            if in_utterance:
                with contextlib.suppress(BaseException):
                    await self._session.abort_stt_stream()
            # WS 切断時に実行中の会話/バージインタスクを孤児化させない。
            # まず 1 ループ回して、既にスケジュール済みで即完了するタスクは走らせ、
            # 本当に I/O 待ち中のタスク（STT→LLM→TTS→再生 の途中など）だけを
            # cancel して await が終わるまで run() から戻らない。
            await asyncio.sleep(0)
            for task in list(self._bg):
                task.cancel()
            for task in list(self._bg):
                with contextlib.suppress(BaseException):
                    await task


class MediaEventRouter:
    """FreeSWITCH イベントを media セッションへ振り分ける。

    着信 AI 応対では dialplan が answer→park までに留めるため、CHANNEL_ANSWER を
    受けた本ルータが ESL API `uuid_audio_stream <uuid> start <ws-url> mono 8k` を
    bgapi で発行して mod_audio_stream を起動する（mod_audio_stream は dialplan app
    ではなく ESL API のため）。対象エージェント id は dialplan が設定した
    チャネル変数 millicall_ai_agent（イベント上は variable_millicall_ai_agent）から読む。

    `esl`/`ws_base_url` 未指定時は起動発行を行わず、従来どおり PLAYBACK_STOP /
    CHANNEL_HANGUP_COMPLETE の振り分けのみを担う（後方互換）。
    """

    def __init__(
        self,
        registry: SessionRegistry,
        *,
        esl=None,
        ws_base_url: str | None = None,
        lock: asyncio.Lock | None = None,
        reconnect=None,
        answer_registry: AnswerRegistry | None = None,
        hangup_registry: HangupRegistry | None = None,
        dtmf_collector: DtmfCollector | None = None,
        workflow_runner=None,
    ) -> None:
        self._registry = registry
        self._answer_registry = answer_registry
        self._hangup_registry = hangup_registry
        self._dtmf_collector = dtmf_collector
        self._workflow_runner = workflow_runner
        self._esl = esl
        # 末尾スラッシュを除去して URL 組み立てを决定論的にする
        self._ws_base_url = ws_base_url.rstrip("/") if ws_base_url else None
        self._lock = lock if lock is not None else asyncio.Lock()
        self._reconnect = reconnect
        # CHANNEL_ANSWER の重複や再発火で二重起動しないよう、起動済み uuid を記録する
        self._started: set[str] = set()
        # バックグラウンドタスクへの強参照を保持し GC による途中破棄を防ぐ。
        self._bg_tasks: set[asyncio.Task] = set()

    async def handle(self, event: dict) -> None:
        name = event.get("Event-Name")
        if name == "PLAYBACK_STOP":
            uuid = event.get("Unique-ID") or event.get("Channel-Call-UUID") or ""
            entry = self._registry.get(uuid)
            if entry is not None:
                _, call_control = entry
                call_control._notify_playback_done()
        elif name == "CHANNEL_ANSWER":
            if self._answer_registry is not None:
                uuid = event.get("Unique-ID") or event.get("Channel-Call-UUID") or ""
                if uuid:
                    self._answer_registry.resolve(uuid)
            await self._maybe_start_audio_stream(event)
            await self._maybe_start_workflow(event)
        elif name == "DTMF":
            uuid = event.get("Unique-ID") or event.get("Channel-Call-UUID") or ""
            digit = event.get("DTMF-Digit") or ""
            if self._dtmf_collector is not None and uuid and digit:
                self._dtmf_collector.feed(uuid, digit)
        elif name == "CHANNEL_HANGUP_COMPLETE":
            uuid = event.get("Channel-Call-UUID") or event.get("Unique-ID") or ""
            if self._hangup_registry is not None and uuid:
                self._hangup_registry.resolve(uuid)
            if self._dtmf_collector is not None and uuid:
                self._dtmf_collector.unregister(uuid)
            self._registry.pop(uuid)
            self._started.discard(uuid)

    async def _maybe_start_audio_stream(self, event: dict) -> None:
        if self._esl is None or self._ws_base_url is None:
            return
        agent = event.get("variable_millicall_ai_agent")
        if not agent:
            return
        uuid = event.get("Unique-ID") or event.get("Channel-Call-UUID") or ""
        if not uuid or uuid in self._started:
            return
        self._started.add(uuid)
        ws_url = f"{self._ws_base_url}/media/audio-fork/{uuid}?agent={agent}"
        command = f"uuid_audio_stream {uuid} start {ws_url} mono 8k"
        try:
            await self._bgapi(command)
        except Exception:
            # 起動に失敗したら再試行できるよう起動済みマークを取り消す
            self._started.discard(uuid)
            logger.exception("failed to start audio stream for uuid=%s", uuid)

    async def _bgapi(self, command: str) -> None:
        """共有 ESL 接続をロックで直列化し、接続断時は reconnect で張り直して再送する。"""
        async with self._lock:
            try:
                await self._esl.bgapi(command)
            except ESLConnectionClosed:
                if self._reconnect is None:
                    raise
                self._esl = await self._reconnect()
                await self._esl.bgapi(command)

    async def _maybe_start_workflow(self, event: dict) -> None:
        """variable_millicall_workflow があればワークフロー実行をバックグラウンドタスクで起動する。"""
        if self._workflow_runner is None:
            return
        workflow_value = event.get("variable_millicall_workflow")
        if not workflow_value:
            return
        uuid = event.get("Unique-ID") or event.get("Channel-Call-UUID") or ""
        if not uuid or uuid in self._started:
            return
        self._started.add(uuid)
        try:
            workflow_id = int(workflow_value)
        except (ValueError, TypeError):
            logger.warning("invalid millicall_workflow value %r for uuid=%s", workflow_value, uuid)
            self._started.discard(uuid)
            return
        task = asyncio.create_task(self._workflow_runner.start(uuid, workflow_id))
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)


async def _build_ephemeral_session(state, call_uuid: str):
    """EphemeralAgentStore から spec + provider を引いて一時セッションを組む（converse 用）。"""
    store = state.ephemeral_store
    entry = store.get_entry(call_uuid)
    if entry is None:
        raise RuntimeError(f"ephemeral agent spec not found for uuid={call_uuid}")
    return await build_conversation_session_from_spec(
        sessionmaker=state.sessionmaker,
        esl=state.esl_command,
        registry=state.session_registry,
        call_uuid=call_uuid,
        spec=entry.spec,
        llm=entry.llm,
        tts=entry.tts,
        stt=entry.stt,
        tts_dir=state.settings.tts_cache_dir,
        transcript=entry.transcript,
        lock=getattr(state, "esl_command_lock", None),
        reconnect=getattr(state, "esl_reconnect", None),
    )


def resolve_ws_agent(agent_param: str) -> tuple[int, bool]:
    """?agent= の値を (agent_id, is_ephemeral) に解決する。

    数値なら DB エージェント（着信と同じ経路、is_ephemeral=False）。
    非数値マーカー（例 "ephemeral"）なら converse の一時エージェント経路
    （is_ephemeral=True、EphemeralAgentStore を call_uuid で引く）。
    """
    try:
        return int(agent_param), False
    except ValueError:
        return 0, True


def register_media_ws(app: FastAPI) -> None:
    @app.websocket("/media/audio-fork/{call_uuid}")
    async def audio_fork_ws(ws: WebSocket, call_uuid: str) -> None:
        await ws.accept()
        agent_id, is_ephemeral = resolve_ws_agent(ws.query_params.get("agent", "0"))
        state = ws.app.state
        try:
            if is_ephemeral:
                session, _ = await _build_ephemeral_session(state, call_uuid)
            else:
                session, _ = await build_conversation_session(
                    sessionmaker=state.sessionmaker,
                    secrets=state.secrets,
                    esl=state.esl_command,
                    registry=state.session_registry,
                    call_uuid=call_uuid,
                    agent_id=agent_id,
                    tts_dir=state.settings.tts_cache_dir,
                    lock=getattr(state, "esl_command_lock", None),
                    reconnect=getattr(state, "esl_reconnect", None),
                )
        except Exception:
            logger.exception("failed to build AI session for uuid=%s", call_uuid)
            await ws.close()
            return
        logger.info(
            "audio-fork: WS 接続確立 (uuid=%s, agent_id=%s, ephemeral=%s)",
            call_uuid,
            agent_id,
            is_ephemeral,
        )
        # VAD パラメータは管理画面（DB）で無停止調整できるため、WS 接続毎に実効設定を読む
        # （settings_service はキャッシュ済みで通常は DB アクセスなし）。
        eff_settings = await effective_settings(state)
        segmenter = VadSegmenter(
            silence_end_ms=session._agent.silence_end_ms,
            mode=getattr(eff_settings, "vad_mode", 2),
            min_rms=getattr(eff_settings, "vad_min_rms", 0),
        )
        handler = AudioForkHandler(session, segmenter)
        try:
            # greet も try 内に置き、raise しても registry/WAV がリークしないようにする。
            await session.greet()
            logger.info("audio-fork: 挨拶再生完了、受信ループを開始 (uuid=%s)", call_uuid)
            await handler.run(ws)
        finally:
            # 通話終了時にセッションが書き出したターン毎 TTS WAV を削除（ディスク枯渇防止）。
            session.cleanup()
            state.session_registry.pop(call_uuid)
