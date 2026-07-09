import asyncio
import logging

import pytest
from fastapi import FastAPI
from starlette.websockets import WebSocketDisconnect

import millicall.media.audio_fork as audio_fork
from millicall.media.audio_fork import AudioForkHandler, register_media_ws
from millicall.media.service import SessionRegistry
from millicall.media.vad import VadEvent


class _FakeWS:
    def __init__(self, frames):
        self._frames = list(frames)

    async def receive_bytes(self):
        if not self._frames:
            raise WebSocketDisconnect(code=1000)
        return self._frames.pop(0)


class _FakeSegmenter:
    def __init__(self, script):
        # script: list[list[VadEvent]] — push 呼び出しごとに返すイベント
        self._script = list(script)

    def push(self, pcm):
        return self._script.pop(0) if self._script else []


class _FakeSession:
    def __init__(self, speaking=False):
        self._speaking = speaking
        self.utterances = []
        self.barge_ins = 0

    @property
    def speaking(self):
        return self._speaking

    async def on_utterance(self, pcm):
        self.utterances.append(pcm)

    async def on_barge_in(self):
        self.barge_ins += 1


@pytest.mark.asyncio
async def test_speech_end_triggers_utterance():
    sess = _FakeSession()
    seg = _FakeSegmenter([[VadEvent("speech_end", b"AUDIO")], []])
    ws = _FakeWS([b"a", b"b"])
    await AudioForkHandler(sess, seg).run(ws)
    await asyncio.sleep(0.01)
    assert sess.utterances == [b"AUDIO"]


@pytest.mark.asyncio
async def test_speech_start_during_ai_speaking_triggers_barge_in():
    sess = _FakeSession(speaking=True)
    seg = _FakeSegmenter([[VadEvent("speech_start")], []])
    ws = _FakeWS([b"a", b"b"])
    await AudioForkHandler(sess, seg).run(ws)
    await asyncio.sleep(0.01)
    assert sess.barge_ins == 1


class _BlockingSession:
    """on_utterance が解除されるまでブロックする — タスク孤児化の検証用。"""

    def __init__(self):
        self._speaking = False
        self.gate = asyncio.Event()
        self.entered = asyncio.Event()
        self.cancelled = False

    @property
    def speaking(self):
        return self._speaking

    async def on_utterance(self, pcm):
        self.entered.set()
        try:
            await self.gate.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise

    async def on_barge_in(self):
        pass


class _DisconnectAfterWS:
    """1 フレーム返した後に disconnect する WS。会話タスク実行中の切断を再現する。"""

    def __init__(self, entered: asyncio.Event):
        self._entered = entered
        self._sent = False

    async def receive_bytes(self):
        if not self._sent:
            self._sent = True
            return b"frame"
        # 会話タスクが起動するまで待ってから切断する。
        await self._entered.wait()
        raise WebSocketDisconnect(code=1000)


@pytest.mark.asyncio
async def test_disconnect_cancels_and_awaits_inflight_conversation_task():
    sess = _BlockingSession()
    seg = _FakeSegmenter([[VadEvent("speech_end", b"AUDIO")]])
    ws = _DisconnectAfterWS(sess.entered)
    handler = AudioForkHandler(sess, seg)
    # run() は実行中の会話タスクを cancel して await するまで戻らない。
    await asyncio.wait_for(handler.run(ws), timeout=1.0)
    assert sess.cancelled is True
    assert handler._current is not None
    assert handler._current.done()


class _RouteSession:
    """register_media_ws 用の最小フェイクセッション。"""

    def __init__(self, greet_exc=None):
        self._agent = type("_A", (), {"silence_end_ms": 600})()
        self._greet_exc = greet_exc
        self.greeted = False
        self.cleaned = 0
        self.speaking = False

    async def greet(self):
        self.greeted = True
        if self._greet_exc is not None:
            raise self._greet_exc

    def cleanup(self):
        self.cleaned += 1

    async def on_utterance(self, pcm):  # pragma: no cover
        pass

    async def on_barge_in(self):  # pragma: no cover
        pass


class _RouteWS:
    def __init__(self, app, frames=None):
        self.app = app
        self.query_params = {"agent": "1"}
        self._frames = list(frames or [])
        self.accepted = False
        self.closed = False

    async def accept(self):
        self.accepted = True

    async def close(self):
        self.closed = True

    async def receive_bytes(self):
        if self._frames:
            return self._frames.pop(0)
        raise WebSocketDisconnect(code=1000)


def _make_app(registry):
    app = FastAPI()

    class _Settings:
        tts_cache_dir = None

    app.state.session_registry = registry
    app.state.sessionmaker = None
    app.state.secrets = None
    app.state.esl_command = None
    app.state.settings = _Settings()
    return app


def _ws_endpoint(app):
    register_media_ws(app)
    route = next(r for r in app.routes if getattr(r, "path", "") == "/media/audio-fork/{call_uuid}")
    return route.endpoint


@pytest.mark.asyncio
async def test_ws_route_cleans_up_session_on_disconnect(monkeypatch):
    # 修正1/2: 正常切断時に cleanup が呼ばれ registry からも除去される。
    registry = SessionRegistry()
    sess = _RouteSession()

    async def _fake_build(**kwargs):
        registry.register("call-1", sess, object())
        return sess, object()

    monkeypatch.setattr(audio_fork, "build_conversation_session", _fake_build)
    app = _make_app(registry)
    endpoint = _ws_endpoint(app)

    await endpoint(_RouteWS(app), "call-1")

    assert sess.greeted is True
    assert sess.cleaned == 1
    assert registry.get("call-1") is None


@pytest.mark.asyncio
async def test_ws_route_greet_failure_does_not_leak_registry(monkeypatch):
    # 修正2: greet が raise しても finally で cleanup + registry pop が走る（リーク防止）。
    registry = SessionRegistry()
    sess = _RouteSession(greet_exc=RuntimeError("greet boom"))

    async def _fake_build(**kwargs):
        registry.register("call-2", sess, object())
        return sess, object()

    monkeypatch.setattr(audio_fork, "build_conversation_session", _fake_build)
    app = _make_app(registry)
    endpoint = _ws_endpoint(app)

    with pytest.raises(RuntimeError, match="greet boom"):
        await endpoint(_RouteWS(app), "call-2")

    assert sess.cleaned == 1
    assert registry.get("call-2") is None


# ── 観測性: タスク例外の ERROR ログ検証 ──────────────────────────────────────


class _ErrorSession:
    """on_utterance が RuntimeError を送出するフェイクセッション（STT/LLM/TTS 失敗の再現）。"""

    def __init__(self, exc: Exception | None = None):
        self._speaking = False
        self._call_uuid = "err-uuid-1"
        self._exc = exc or RuntimeError("stt boom")

    @property
    def speaking(self):
        return self._speaking

    async def on_utterance(self, pcm):
        raise self._exc

    async def on_barge_in(self):
        pass


@pytest.mark.asyncio
async def test_spawn_logs_error_on_task_exception(caplog):
    """on_utterance タスクが例外を投げた場合に ERROR ログ（uuid 含む, exc_info 付き）が出ること。"""
    # alembic fileConfig が disable_existing_loggers=True でロガーを無効化することがあるため
    # 明示的に有効化してから caplog で捕捉する（test_call_control.py の既存パターンに倣う）。
    target_logger = logging.getLogger("millicall.media.audio_fork")
    target_logger.disabled = False

    sess = _ErrorSession(RuntimeError("stt boom"))
    seg = _FakeSegmenter([[VadEvent("speech_end", b"AUDIO")], []])
    ws = _FakeWS([b"frame1", b"frame2"])

    with caplog.at_level(logging.ERROR, logger="millicall.media.audio_fork"):
        await AudioForkHandler(sess, seg).run(ws)
        # done_callback はタスク完了直後のイベントループ tick で呼ばれるため 1 tick 待つ。
        await asyncio.sleep(0)

    # ERROR レコードに uuid と例外メッセージが含まれること。
    error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert error_records, f"ERROR ログが出ていない。records={caplog.records}"
    combined = " ".join(r.message for r in error_records)
    assert "err-uuid-1" in combined, f"uuid が ERROR ログに含まれない: {combined!r}"
    # exc_info が付いているか（exc_text にトレースバックが入る）を確認。
    assert any(r.exc_info and r.exc_info[1] is not None for r in error_records), (
        "exc_info が ERROR レコードに付いていない"
    )


@pytest.mark.asyncio
async def test_spawn_does_not_log_on_cancelled_error(caplog):
    """CancelledError は正常系（WS 切断時のキャンセル）なので ERROR ログを出さないこと。"""
    target_logger = logging.getLogger("millicall.media.audio_fork")
    target_logger.disabled = False

    sess = _BlockingSession()  # WS 切断で cancel される
    seg = _FakeSegmenter([[VadEvent("speech_end", b"AUDIO")]])
    ws = _DisconnectAfterWS(sess.entered)

    with caplog.at_level(logging.ERROR, logger="millicall.media.audio_fork"):
        await asyncio.wait_for(AudioForkHandler(sess, seg).run(ws), timeout=1.0)
        await asyncio.sleep(0)

    error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert not error_records, f"CancelledError で ERROR ログが出てはならない: {error_records}"
