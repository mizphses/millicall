import asyncio

import pytest
from starlette.websockets import WebSocketDisconnect

from millicall.media.audio_fork import AudioForkHandler
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
