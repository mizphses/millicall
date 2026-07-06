"""Phase 4a Important 修正: 共有 ESL 接続への非直列化 bgapi の直列化 + reconnect。

OutboundCallService（dial/converse originate/uuid_kill）と CallPrimitives
（uuid_record start/stop）が共有 `app.state.esl_command` に対し直接 bgapi する
経路を、EslCallControl._bgapi と同型の「共有 lock で直列化 + ESLConnectionClosed
時に reconnect で張り直して再送」パターンに揃える（プラン不変条件 I6）。

RED フェーズでは lock/reconnect が未配線のため、直列化されず max_concurrent>1、
または reconnect されずに ESLConnectionClosed が伝播して失敗する。
"""

import asyncio

import pytest

from millicall.mcp_server.outbound import OutboundCallService
from millicall.mcp_server.primitives import CallPrimitives
from millicall.media.service import AnswerRegistry, HangupRegistry
from millicall.telephony.esl import ESLConnectionClosed


class _SlowEsl:
    """bgapi が同時に走ると max_concurrent>1 になる、直列化検証用フェイク。"""

    def __init__(self) -> None:
        self.max_concurrent = 0
        self.active = 0
        self.cmds: list[str] = []

    async def bgapi(self, command: str) -> str:
        self.active += 1
        self.max_concurrent = max(self.max_concurrent, self.active)
        await asyncio.sleep(0.01)
        self.active -= 1
        self.cmds.append(command)
        return ""


class _DeadThenAliveEsl:
    def __init__(self, alive: bool) -> None:
        self.alive = alive
        self.cmds: list[str] = []

    async def bgapi(self, command: str) -> str:
        if not self.alive:
            raise ESLConnectionClosed("dead")
        self.cmds.append(command)
        return ""


class _FakeTrunk:
    def __init__(self, name: str, caller_id: str = "") -> None:
        self.name = name
        self.caller_id = caller_id
        self.enabled = True


def _make_outbound(esl, *, lock=None, reconnect=None, answer_reg=None):
    async def _fetch_enabled_trunks():
        return [_FakeTrunk("main", caller_id="0312345678")]

    return OutboundCallService(
        esl=esl,
        answer_registry=answer_reg or AnswerRegistry(),
        sip_domain="millicall.local",
        fetch_enabled_trunks=_fetch_enabled_trunks,
        uuid_factory=lambda: "fixed-uuid",
        lock=lock,
        reconnect=reconnect,
    )


# -- OutboundCallService.dial: 直列化 -----------------------------------------


@pytest.mark.asyncio
async def test_dial_serializes_originate_under_shared_lock():
    """並行 dial の originate が共有ロックで直列化される（max_concurrent==1）。"""
    esl = _SlowEsl()
    shared_lock = asyncio.Lock()

    ans1 = AnswerRegistry()
    ans2 = AnswerRegistry()
    svc1 = _make_outbound(esl, lock=shared_lock, answer_reg=ans1)
    svc2 = _make_outbound(esl, lock=shared_lock, answer_reg=ans2)

    async def _drive(svc, reg):
        task = asyncio.ensure_future(svc.dial("0901234567", timeout=1.0))
        # dial 内で register が走った後に resolve する。
        await asyncio.sleep(0.005)
        reg.resolve("fixed-uuid")
        return await task

    await asyncio.gather(_drive(svc1, ans1), _drive(svc2, ans2))
    assert esl.max_concurrent == 1


@pytest.mark.asyncio
async def test_dial_reconnects_on_closed_connection():
    """dial の originate が接続断なら reconnect で張り直して再送し、esl 参照も更新。"""
    fresh = _DeadThenAliveEsl(alive=True)

    async def _reconnect():
        return fresh

    dead = _DeadThenAliveEsl(alive=False)
    ans = AnswerRegistry()
    svc = _make_outbound(dead, reconnect=_reconnect, answer_reg=ans)

    task = asyncio.ensure_future(svc.dial("0901234567", timeout=1.0))
    await asyncio.sleep(0.005)
    ans.resolve("fixed-uuid")
    result = await task
    assert result.call_uuid == "fixed-uuid"
    assert fresh.cmds and fresh.cmds[0].startswith("originate ")
    assert svc._esl is fresh


@pytest.mark.asyncio
async def test_dial_no_reconnect_reraises_on_closed():
    """reconnect 未注入なら ESLConnectionClosed が伝播する（後方互換）。"""
    dead = _DeadThenAliveEsl(alive=False)
    svc = _make_outbound(dead)
    with pytest.raises(ESLConnectionClosed):
        await svc.dial("0901234567", timeout=1.0)


# -- OutboundCallService.converse: uuid_kill フォールバックの直列化/reconnect ---


@pytest.mark.asyncio
async def test_converse_uuid_kill_uses_lock_and_reconnect():
    """converse の uuid_kill フォールバックも lock 経由・接続断で reconnect 再送する。"""
    from millicall.mcp_server.ephemeral import EphemeralAgentStore

    fresh = _DeadThenAliveEsl(alive=True)

    async def _reconnect():
        return fresh

    # originate は生き、応答も来るが hangup は来ない → uuid_kill フォールバック。
    class _KillDeadEsl:
        def __init__(self) -> None:
            self.cmds: list[str] = []

        async def bgapi(self, command: str) -> str:
            if command.startswith("uuid_kill"):
                raise ESLConnectionClosed("dead")
            self.cmds.append(command)
            return ""

    esl = _KillDeadEsl()
    ans = AnswerRegistry()
    hang = HangupRegistry()
    store = EphemeralAgentStore()

    async def _fetch_enabled_trunks():
        return [_FakeTrunk("main", caller_id="0312345678")]

    svc = OutboundCallService(
        esl=esl,
        answer_registry=ans,
        sip_domain="millicall.local",
        fetch_enabled_trunks=_fetch_enabled_trunks,
        uuid_factory=lambda: "fixed-uuid",
        hangup_registry=hang,
        ephemeral_store=store,
        reconnect=_reconnect,
    )
    # hangup は解決しない → wait が timeout → uuid_kill フォールバック。
    task = asyncio.ensure_future(
        svc.converse("0901234567", purpose="test", max_conversation_seconds=0.01)
    )
    await asyncio.sleep(0.005)
    ans.resolve("fixed-uuid")
    result = await task
    assert result.status == "completed"
    assert fresh.cmds == ["uuid_kill fixed-uuid"]
    assert svc._esl is fresh


# -- CallPrimitives.listen: uuid_record start/stop の直列化/reconnect ----------


class _FakeTTS:
    async def synthesize(self, text: str) -> bytes:
        return b"\x00\x00" * 8000


class _FakeSTTSession:
    async def feed(self, pcm: bytes) -> None:
        pass

    async def finish(self) -> str:
        return ""


class _FakeSTT:
    def open_session(self):
        return _FakeSTTSession()


class _FakeCallControl:
    async def play_file(self, path: str) -> None:
        pass


def _make_primitives(esl, tts_dir, *, lock=None, reconnect=None):
    async def _read_recording(path):
        return b""

    async def _sleep(secs):
        pass

    return CallPrimitives(
        esl=esl,
        call_uuid="fixed-uuid",
        call_control=_FakeCallControl(),
        tts=_FakeTTS(),
        stt=_FakeSTT(),
        tts_dir=tts_dir,
        sleep=_sleep,
        read_recording=_read_recording,
        lock=lock,
        reconnect=reconnect,
    )


@pytest.mark.asyncio
async def test_listen_serializes_record_under_shared_lock(tmp_path):
    """並行 listen の uuid_record start/stop が共有ロックで直列化される。"""
    esl = _SlowEsl()
    shared_lock = asyncio.Lock()
    prim1 = _make_primitives(esl, tmp_path, lock=shared_lock)
    prim2 = _make_primitives(esl, tmp_path, lock=shared_lock)
    await asyncio.gather(prim1.listen(max_seconds=1), prim2.listen(max_seconds=1))
    assert esl.max_concurrent == 1


@pytest.mark.asyncio
async def test_listen_reconnects_on_closed_connection(tmp_path):
    """listen の uuid_record start が接続断なら reconnect で張り直して再送する。"""
    fresh = _DeadThenAliveEsl(alive=True)

    async def _reconnect():
        return fresh

    dead = _DeadThenAliveEsl(alive=False)
    prim = _make_primitives(dead, tmp_path, reconnect=_reconnect)
    await prim.listen(max_seconds=1)
    # start と stop の両方が fresh 上で発行される。
    assert any(c.startswith("uuid_record fixed-uuid start") for c in fresh.cmds)
    assert any(c.startswith("uuid_record fixed-uuid stop") for c in fresh.cmds)
    assert prim._esl is fresh


@pytest.mark.asyncio
async def test_listen_no_reconnect_reraises_on_closed(tmp_path):
    """reconnect 未注入なら ESLConnectionClosed が伝播する（後方互換）。"""
    dead = _DeadThenAliveEsl(alive=False)
    prim = _make_primitives(dead, tmp_path)
    with pytest.raises(ESLConnectionClosed):
        await prim.listen(max_seconds=1)
