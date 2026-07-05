import pytest

from millicall.media.audio_fork import MediaEventRouter
from millicall.media.service import SessionRegistry


class _CC:
    def __init__(self):
        self.done = 0

    def _notify_playback_done(self):
        self.done += 1


@pytest.mark.asyncio
async def test_playback_stop_notifies_call_control():
    reg = SessionRegistry()
    cc = _CC()
    reg.register("u1", session=object(), call_control=cc)
    router = MediaEventRouter(reg)
    await router.handle({"Event-Name": "PLAYBACK_STOP", "Unique-ID": "u1"})
    assert cc.done == 1


@pytest.mark.asyncio
async def test_hangup_removes_session():
    reg = SessionRegistry()
    reg.register("u1", session=object(), call_control=_CC())
    router = MediaEventRouter(reg)
    await router.handle({"Event-Name": "CHANNEL_HANGUP_COMPLETE", "Channel-Call-UUID": "u1"})
    assert reg.get("u1") is None


@pytest.mark.asyncio
async def test_playback_stop_unknown_uuid_is_noop():
    reg = SessionRegistry()
    router = MediaEventRouter(reg)
    # 未登録 uuid でも例外なく無視される。
    await router.handle({"Event-Name": "PLAYBACK_STOP", "Unique-ID": "missing"})


class _FakeEsl:
    def __init__(self):
        self.commands = []

    async def bgapi(self, command):
        self.commands.append(command)
        return "+OK"


@pytest.mark.asyncio
async def test_channel_answer_starts_audio_stream_for_ai_agent():
    reg = SessionRegistry()
    esl = _FakeEsl()
    router = MediaEventRouter(reg, esl=esl, ws_base_url="ws://127.0.0.1:8000")
    await router.handle(
        {
            "Event-Name": "CHANNEL_ANSWER",
            "Unique-ID": "abc",
            "variable_millicall_ai_agent": "7",
        }
    )
    assert esl.commands == [
        "uuid_audio_stream abc start ws://127.0.0.1:8000/media/audio-fork/abc?agent=7 mono 8k"
    ]


@pytest.mark.asyncio
async def test_channel_answer_without_ai_agent_var_is_noop():
    reg = SessionRegistry()
    esl = _FakeEsl()
    router = MediaEventRouter(reg, esl=esl, ws_base_url="ws://127.0.0.1:8000")
    await router.handle({"Event-Name": "CHANNEL_ANSWER", "Unique-ID": "abc"})
    assert esl.commands == []


@pytest.mark.asyncio
async def test_channel_answer_is_idempotent_per_uuid():
    reg = SessionRegistry()
    esl = _FakeEsl()
    router = MediaEventRouter(reg, esl=esl, ws_base_url="ws://127.0.0.1:8000")
    ev = {
        "Event-Name": "CHANNEL_ANSWER",
        "Unique-ID": "abc",
        "variable_millicall_ai_agent": "7",
    }
    await router.handle(ev)
    await router.handle(ev)
    assert len(esl.commands) == 1


@pytest.mark.asyncio
async def test_channel_answer_without_esl_configured_is_noop():
    # esl/ws 未設定（既存の構築経路との後方互換）でも例外を出さない。
    reg = SessionRegistry()
    router = MediaEventRouter(reg)
    await router.handle(
        {
            "Event-Name": "CHANNEL_ANSWER",
            "Unique-ID": "abc",
            "variable_millicall_ai_agent": "7",
        }
    )
