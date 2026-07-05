"""E2E: 偽 audio_fork クライアントで会話ループ全体を通し、レイテンシ予算を検証する。

音声フレーム列 → 実 VadSegmenter（スクリプト判定器）→ 実 AudioForkHandler →
実 ConversationSession（偽 STT/LLM/TTS + 偽 CallControl + 注入クロック）を結線し、
発話終端 → STT → LLM → 文分割 → TTS → 再生 の 1 ターン完了と、
発話終端→初回再生のレイテンシが目標 1 秒未満（決定的に 80ms）に収まることを検証する。

実装との整合（brief からの逸脱は task-18-report.md 参照）:
    - on_turn は単一タプル (role, text, latency_ms) を受ける実契約に合わせる。
    - ConversationSession.greet() が clock を 1 回消費するため、注入 tick 列を調整。
    - _FRAME は VadSegmenter の 30ms/8k=480byte フレームに一致させる（1 WS フレーム
      = 1 VAD フレーム）。
    - AudioForkHandler.run() は WS 切断時に実行中タスクを cancel するため、
      偽 WS はターン完了（assistant ターン発火）まで切断を遅延させる。
"""

import asyncio
from pathlib import Path

import pytest
from starlette.websockets import WebSocketDisconnect

from millicall.media.audio_fork import AudioForkHandler
from millicall.media.conversation import ConversationSession
from millicall.media.vad import VadSegmenter

# 30ms @8k = 240 samples = 480 bytes（VadSegmenter のフレーム境界に一致させる）
_FRAME = b"\x11\x11" * 240


class _ScriptedClassifier:
    def __init__(self, pattern):
        self._p = pattern
        self._i = 0

    def is_speech(self, frame, rate):
        v = self._p[min(self._i, len(self._p) - 1)]
        self._i += 1
        return v


class _FakeWS:
    """全フレームを流したのち、ターン完了を待って切断する偽 WS。

    receive_bytes はフレーム返却時に一切サスペンドしないため、run() ループは
    全フレームを同期的に消費し on_utterance を spawn する。フレーム枯渇後は
    done イベント（assistant ターン発火で set）を待ってから切断することで、
    run() の finally による会話タスク cancel を回避し、ターンを完走させる。
    """

    def __init__(self, frames, done: asyncio.Event):
        self._frames = list(frames)
        self._done = done

    async def receive_bytes(self):
        if self._frames:
            return self._frames.pop(0)
        await self._done.wait()
        raise WebSocketDisconnect(code=1000)


class _Agent:
    id = 1
    system_prompt = "あなたは受付です"
    greeting = "お電話ありがとうございます。"
    max_history = 10
    silence_end_ms = 600


class _STT:
    def open_session(self):
        return self

    async def feed(self, pcm):
        pass

    async def finish(self):
        return "予約したいのですが"


class _LLM:
    async def stream_chat(self, messages):
        for t in ["はい", "、", "承知しました。", "ご希望日は", "いつですか？"]:
            yield t


class _TTS:
    async def synthesize(self, text):
        return b"\x00\x00" * 80


class _CC:
    def __init__(self):
        self.played = []
        self.hung = 0

    async def play_file(self, path):
        self.played.append(path)

    async def stop_playback(self):
        pass

    async def hangup(self):
        self.hung += 1


@pytest.mark.asyncio
async def test_full_turn_e2e_under_latency_budget(tmp_path):
    turns = []
    done = asyncio.Event()

    async def _on_turn(turn):
        turns.append(turn)
        if turn[0] == "assistant":
            done.set()

    # 注入クロック（greet が 1 回消費する分を先頭に置く）:
    #   greet=任意 / 発話終端=1.0 / 初回再生=1.08（=80ms）
    ticks = iter([0.0, 1.0] + [1.08] * 50)

    def _clock():
        try:
            return next(ticks)
        except StopIteration:
            return 1.08

    session = ConversationSession(
        agent=_Agent(),
        stt=_STT(),
        llm=_LLM(),
        tts=_TTS(),
        call_control=_CC(),
        tts_dir=Path(tmp_path),
        call_uuid="e2e",
        on_turn=_on_turn,
        clock=_clock,
    )
    # 3フレーム無音→15フレーム発話→25フレーム無音（600ms=20フレームで終端）
    pattern = [False] * 3 + [True] * 15 + [False] * 25
    seg = VadSegmenter(classifier=_ScriptedClassifier(pattern), silence_end_ms=600)
    handler = AudioForkHandler(session, seg)

    await session.greet()
    frames = [_FRAME] * len(pattern)
    await asyncio.wait_for(handler.run(_FakeWS(frames, done)), timeout=2.0)
    if handler._current is not None:
        await asyncio.wait_for(handler._current, timeout=2.0)

    cc = session._call_control
    # greeting(1) + 応答2文（「はい、承知しました。」「ご希望日はいつですか？」）
    assert len(cc.played) >= 3
    assistant = next(t for t in turns if t[0] == "assistant")
    assert assistant[1] == "はい、承知しました。ご希望日はいつですか？"
    assert assistant[2] < 1000  # レイテンシ目標: 発話終端→初回再生 < 1秒
    assert assistant[2] == 80
