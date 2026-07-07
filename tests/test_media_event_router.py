import asyncio

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


# --------------------------------------------------------------------------- #
# DTMF イベント配線 (Task 6)
# --------------------------------------------------------------------------- #


class _FakeDtmfCollector:
    """フェイク DtmfCollector — feed/unregister の呼び出しを記録する。"""

    def __init__(self) -> None:
        self.fed: list[tuple[str, str]] = []
        self.unregistered: list[str] = []

    def feed(self, uuid: str, digit: str) -> None:
        self.fed.append((uuid, digit))

    def unregister(self, uuid: str) -> None:
        self.unregistered.append(uuid)


@pytest.mark.asyncio
async def test_dtmf_event_calls_collector_feed():
    """DTMF イベントが dtmf_collector.feed(uuid, digit) を呼ぶ。"""
    reg = SessionRegistry()
    collector = _FakeDtmfCollector()
    router = MediaEventRouter(reg, dtmf_collector=collector)
    await router.handle(
        {
            "Event-Name": "DTMF",
            "Unique-ID": "abc",
            "DTMF-Digit": "5",
        }
    )
    assert collector.fed == [("abc", "5")]


@pytest.mark.asyncio
async def test_dtmf_event_uses_channel_call_uuid_fallback():
    """Unique-ID がない場合は Channel-Call-UUID を uuid として使う。"""
    reg = SessionRegistry()
    collector = _FakeDtmfCollector()
    router = MediaEventRouter(reg, dtmf_collector=collector)
    await router.handle(
        {
            "Event-Name": "DTMF",
            "Channel-Call-UUID": "xyz",
            "DTMF-Digit": "3",
        }
    )
    assert collector.fed == [("xyz", "3")]


@pytest.mark.asyncio
async def test_dtmf_event_without_collector_is_noop():
    """dtmf_collector 未注入（None）でも例外を出さない（後方互換）。"""
    reg = SessionRegistry()
    router = MediaEventRouter(reg)  # dtmf_collector=None
    # 例外なく処理される
    await router.handle(
        {
            "Event-Name": "DTMF",
            "Unique-ID": "abc",
            "DTMF-Digit": "1",
        }
    )


@pytest.mark.asyncio
async def test_dtmf_event_empty_digit_is_noop():
    """DTMF-Digit が空文字の場合は feed を呼ばない。"""
    reg = SessionRegistry()
    collector = _FakeDtmfCollector()
    router = MediaEventRouter(reg, dtmf_collector=collector)
    await router.handle(
        {
            "Event-Name": "DTMF",
            "Unique-ID": "abc",
            "DTMF-Digit": "",
        }
    )
    assert collector.fed == []


@pytest.mark.asyncio
async def test_hangup_calls_dtmf_collector_unregister():
    """CHANNEL_HANGUP_COMPLETE が dtmf_collector.unregister(uuid) を呼ぶ。"""
    reg = SessionRegistry()
    reg.register("u1", session=object(), call_control=_CC())
    collector = _FakeDtmfCollector()
    router = MediaEventRouter(reg, dtmf_collector=collector)
    await router.handle(
        {"Event-Name": "CHANNEL_HANGUP_COMPLETE", "Channel-Call-UUID": "u1"}
    )
    assert "u1" in collector.unregistered


@pytest.mark.asyncio
async def test_hangup_without_dtmf_collector_still_removes_session():
    """dtmf_collector が None でもハングアップ時にセッションが正しく削除される。"""
    reg = SessionRegistry()
    reg.register("u1", session=object(), call_control=_CC())
    router = MediaEventRouter(reg)  # dtmf_collector=None
    await router.handle(
        {"Event-Name": "CHANNEL_HANGUP_COMPLETE", "Channel-Call-UUID": "u1"}
    )
    assert reg.get("u1") is None


# --------------------------------------------------------------------------- #
# Task 9: workflow 起動テスト
# --------------------------------------------------------------------------- #


class _FakeRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    async def start(self, uuid: str, wf_id: int) -> None:
        self.calls.append((uuid, wf_id))


@pytest.mark.asyncio
async def test_channel_answer_starts_workflow():
    """variable_millicall_workflow があると workflow_runner.start が呼ばれる。"""
    reg = SessionRegistry()
    runner = _FakeRunner()
    router = MediaEventRouter(reg, workflow_runner=runner)
    await router.handle(
        {
            "Event-Name": "CHANNEL_ANSWER",
            "Unique-ID": "wf-uuid",
            "variable_millicall_workflow": "5",
        }
    )
    await asyncio.sleep(0)  # create_task を走らせる
    assert runner.calls == [("wf-uuid", 5)]


@pytest.mark.asyncio
async def test_channel_answer_workflow_no_double_dispatch():
    """同じイベントを 2 回受けても runner.start は 1 回しか呼ばれない。"""
    reg = SessionRegistry()
    runner = _FakeRunner()
    router = MediaEventRouter(reg, workflow_runner=runner)
    ev = {
        "Event-Name": "CHANNEL_ANSWER",
        "Unique-ID": "wf-uuid-dup",
        "variable_millicall_workflow": "5",
    }
    await router.handle(ev)
    await router.handle(ev)
    await asyncio.sleep(0)
    assert len(runner.calls) == 1


@pytest.mark.asyncio
async def test_channel_answer_workflow_and_agent_not_both():
    """ai_agent と workflow 両方の変数があっても二重起動しない (audio_stream が先に _started に追加)。"""
    reg = SessionRegistry()
    esl = _FakeEsl()
    runner = _FakeRunner()
    router = MediaEventRouter(
        reg,
        esl=esl,
        ws_base_url="ws://127.0.0.1:8000",
        workflow_runner=runner,
    )
    await router.handle(
        {
            "Event-Name": "CHANNEL_ANSWER",
            "Unique-ID": "both-uuid",
            "variable_millicall_ai_agent": "3",
            "variable_millicall_workflow": "7",
        }
    )
    await asyncio.sleep(0)
    # audio stream は起動されている
    assert any("uuid_audio_stream both-uuid" in cmd for cmd in esl.commands)
    # workflow は起動されていない（uuid が _started に追加済み）
    assert runner.calls == []


@pytest.mark.asyncio
async def test_channel_answer_invalid_workflow_id():
    """variable_millicall_workflow が整数でない場合はクラッシュせず runner.start も呼ばれない。"""
    reg = SessionRegistry()
    runner = _FakeRunner()
    router = MediaEventRouter(reg, workflow_runner=runner)
    await router.handle(
        {
            "Event-Name": "CHANNEL_ANSWER",
            "Unique-ID": "bad-uuid",
            "variable_millicall_workflow": "not-an-int",
        }
    )
    await asyncio.sleep(0)
    assert runner.calls == []
