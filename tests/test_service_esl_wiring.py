"""
I6 準拠ギャップ修正: build_conversation_session が lock/reconnect を
EslCallControl に正しく配線することを検証するユニットテスト。

RED フェーズでは lock/reconnect 引数が存在しないため TypeError で失敗する。
"""
import asyncio
from unittest.mock import MagicMock, patch

import pytest

from millicall.media.service import SessionRegistry, build_conversation_session
from millicall.models import AiAgent, Provider

# ---- テスト用フェイク ----


class _FakeEsl:
    async def bgapi(self, command: str) -> str:
        return "job-uuid"


class _FakeAgent:
    """AiAgent の属性だけ持つデータオブジェクト（ORM 不使用）。"""

    id = 1
    name = "test-agent"
    system_prompt = "You are a helpful assistant."
    greeting = "こんにちは"
    llm_provider_id = 10
    tts_provider_id = 20
    stt_provider_id = 30
    max_history = 10
    silence_end_ms = 600


class _FakeProvider:
    """Provider の属性だけ持つデータオブジェクト。"""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self.config_json = "{}"
        self.api_key_encrypted = None


class _FakeDb:
    """AsyncSession の最小フェイク。"""

    def __init__(self) -> None:
        agent = _FakeAgent()
        self._store = {
            (AiAgent, 1): agent,
            (Provider, 10): _FakeProvider("openai_compatible"),
            (Provider, 20): _FakeProvider("voicevox"),
            (Provider, 30): _FakeProvider("whisper"),
        }

    async def get(self, model, pk):
        return self._store.get((model, pk))

    def add(self, obj) -> None:
        pass

    async def commit(self) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_) -> None:
        pass


class _FakeSessionmaker:
    """async_sessionmaker のフェイク。呼び出すと _FakeDb を返す。"""

    def __call__(self):
        return _FakeDb()


class _FakeSecrets:
    master_key = "a" * 48  # SecretBox が要求する任意の文字列


def _fake_build_something(*_args, **_kwargs):
    return MagicMock()


# ---- テスト ----


@pytest.mark.asyncio
async def test_build_conversation_session_passes_lock_to_esl_call_control(tmp_path):
    """build_conversation_session に lock を渡すと EslCallControl に配線される（I6）。"""
    registry = SessionRegistry()
    esl = _FakeEsl()
    shared_lock = asyncio.Lock()

    with (
        patch("millicall.media.service.ai_registry.build_llm", _fake_build_something),
        patch("millicall.media.service.ai_registry.build_tts", _fake_build_something),
        patch("millicall.media.service.ai_registry.build_stt", _fake_build_something),
        patch("millicall.media.service.ConversationSession", MagicMock()),
    ):
        _session, call_control = await build_conversation_session(
            sessionmaker=_FakeSessionmaker(),
            secrets=_FakeSecrets(),
            esl=esl,
            registry=registry,
            call_uuid="uuid-lock-test",
            agent_id=1,
            tts_dir=tmp_path,
            lock=shared_lock,
        )

    assert call_control._lock is shared_lock, (
        "EslCallControl._lock は渡された共有ロックと同一オブジェクトでなければならない"
    )


@pytest.mark.asyncio
async def test_build_conversation_session_passes_reconnect_to_esl_call_control(tmp_path):
    """build_conversation_session に reconnect を渡すと EslCallControl に配線される（I6）。"""
    registry = SessionRegistry()
    esl = _FakeEsl()

    async def _reconnect():
        return _FakeEsl()

    with (
        patch("millicall.media.service.ai_registry.build_llm", _fake_build_something),
        patch("millicall.media.service.ai_registry.build_tts", _fake_build_something),
        patch("millicall.media.service.ai_registry.build_stt", _fake_build_something),
        patch("millicall.media.service.ConversationSession", MagicMock()),
    ):
        _session, call_control = await build_conversation_session(
            sessionmaker=_FakeSessionmaker(),
            secrets=_FakeSecrets(),
            esl=esl,
            registry=registry,
            call_uuid="uuid-reconnect-test",
            agent_id=1,
            tts_dir=tmp_path,
            reconnect=_reconnect,
        )

    assert call_control._reconnect is _reconnect, (
        "EslCallControl._reconnect は渡された reconnect コールバックと同一でなければならない"
    )


@pytest.mark.asyncio
async def test_build_conversation_session_without_lock_reconnect_defaults_to_none(tmp_path):
    """lock/reconnect を省略した場合、EslCallControl に None が渡される（後方互換）。"""
    registry = SessionRegistry()
    esl = _FakeEsl()

    with (
        patch("millicall.media.service.ai_registry.build_llm", _fake_build_something),
        patch("millicall.media.service.ai_registry.build_tts", _fake_build_something),
        patch("millicall.media.service.ai_registry.build_stt", _fake_build_something),
        patch("millicall.media.service.ConversationSession", MagicMock()),
    ):
        _session, call_control = await build_conversation_session(
            sessionmaker=_FakeSessionmaker(),
            secrets=_FakeSecrets(),
            esl=esl,
            registry=registry,
            call_uuid="uuid-compat-test",
            agent_id=1,
            tts_dir=tmp_path,
        )

    # lock 省略時は EslCallControl が内部で asyncio.Lock() を生成する（_reconnect は None）
    assert call_control._reconnect is None, "lock/reconnect 省略時は reconnect が None"
