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

from millicall.media.service import SessionRegistry, build_conversation_session
from millicall.media.vad import VadSegmenter

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
    """FreeSWITCH イベントを media セッションへ振り分ける。"""

    def __init__(self, registry: SessionRegistry) -> None:
        self._registry = registry

    async def handle(self, event: dict) -> None:
        name = event.get("Event-Name")
        if name == "PLAYBACK_STOP":
            uuid = event.get("Unique-ID") or event.get("Channel-Call-UUID") or ""
            entry = self._registry.get(uuid)
            if entry is not None:
                _, call_control = entry
                call_control._notify_playback_done()
        elif name == "CHANNEL_HANGUP_COMPLETE":
            uuid = event.get("Channel-Call-UUID") or event.get("Unique-ID") or ""
            self._registry.pop(uuid)


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
            )
        except Exception:
            logger.exception("failed to build AI session for uuid=%s", call_uuid)
            await ws.close()
            return
        segmenter = VadSegmenter(silence_end_ms=session._agent.silence_end_ms)
        handler = AudioForkHandler(session, segmenter)
        await session.greet()
        try:
            await handler.run(ws)
        finally:
            state.session_registry.pop(call_uuid)
