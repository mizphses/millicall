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
