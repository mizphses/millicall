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

from millicall.media.service import (
    AnswerRegistry,
    SessionRegistry,
    build_conversation_session,
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
        return task

    async def run(self, ws) -> None:
        try:
            while True:
                frame = await ws.receive_bytes()
                for ev in self._segmenter.push(frame):
                    if ev.kind == "speech_start":
                        if self._session.speaking:
                            self._spawn(self._session.on_barge_in())
                    elif ev.kind == "speech_end" and (
                        self._current is None or self._current.done()
                    ):
                        self._current = self._spawn(self._session.on_utterance(ev.audio))
        except WebSocketDisconnect:
            return
        finally:
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
    ) -> None:
        self._registry = registry
        self._answer_registry = answer_registry
        self._esl = esl
        # 末尾スラッシュを除去して URL 組み立てを决定論的にする
        self._ws_base_url = ws_base_url.rstrip("/") if ws_base_url else None
        self._lock = lock if lock is not None else asyncio.Lock()
        self._reconnect = reconnect
        # CHANNEL_ANSWER の重複や再発火で二重起動しないよう、起動済み uuid を記録する
        self._started: set[str] = set()

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
        elif name == "CHANNEL_HANGUP_COMPLETE":
            uuid = event.get("Channel-Call-UUID") or event.get("Unique-ID") or ""
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


def register_media_ws(app: FastAPI) -> None:
    @app.websocket("/media/audio-fork/{call_uuid}")
    async def audio_fork_ws(ws: WebSocket, call_uuid: str) -> None:
        await ws.accept()
        agent_id = int(ws.query_params.get("agent", "0"))
        state = ws.app.state
        try:
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
        segmenter = VadSegmenter(silence_end_ms=session._agent.silence_end_ms)
        handler = AudioForkHandler(session, segmenter)
        try:
            # greet も try 内に置き、raise しても registry/WAV がリークしないようにする。
            await session.greet()
            await handler.run(ws)
        finally:
            # 通話終了時にセッションが書き出したターン毎 TTS WAV を削除（ディスク枯渇防止）。
            session.cleanup()
            state.session_registry.pop(call_uuid)
