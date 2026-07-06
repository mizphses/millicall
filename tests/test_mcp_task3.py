"""Task 3: 発信オーケストレーション + 手動音声プリミティブのテスト。

受入条件:
  - AnswerRegistry が CHANNEL_ANSWER 完了 future を解決する。
  - MediaEventRouter が CHANNEL_ANSWER で AnswerRegistry を解決する。
  - OutboundCallService.dial が originate {...&park} コマンド文字列を発行し
    （トランク自動選択・186 通知・origination_uuid）、answer 待ち後 call_uuid を返す。
  - 30s タイムアウト時は §1 タイムアウト JSON 相当の TimeoutError。
  - CallPrimitives.say が TTS 合成→WAV 書き出し→play_file を行う。
  - CallPrimitives.listen が単発録音→STT を返し録音を stop する（fake STT）。
  - say_and_listen が say → listen を合成する。
  - 実 FS 不要・実時間 sleep 回避（injectable sleep / tmp_path）。
"""
import asyncio
import wave
from io import BytesIO
from pathlib import Path

import pytest

from millicall.media.audio_fork import MediaEventRouter
from millicall.media.service import AnswerRegistry, SessionRegistry

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeEsl:
    def __init__(self) -> None:
        self.cmds: list[str] = []
        self.api_cmds: list[str] = []

    async def bgapi(self, command: str) -> str:
        self.cmds.append(command)
        return "job-uuid"

    async def api(self, command: str) -> str:
        self.api_cmds.append(command)
        return "+OK"


def _wav_bytes(pcm: bytes = b"\x00\x00" * 800) -> bytes:
    buf = BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(pcm)
    return buf.getvalue()


# ===========================================================================
# AnswerRegistry
# ===========================================================================


@pytest.mark.asyncio
async def test_answer_registry_resolves_on_answer():
    reg = AnswerRegistry()
    fut = reg.register("u1")
    reg.resolve("u1")
    assert await asyncio.wait_for(fut, timeout=1) is True


@pytest.mark.asyncio
async def test_answer_registry_wait_returns_true_when_resolved():
    reg = AnswerRegistry()
    reg.register("u1")

    async def _resolve_soon():
        await asyncio.sleep(0)
        reg.resolve("u1")

    asyncio.get_running_loop().create_task(_resolve_soon())
    assert await reg.wait("u1", timeout=1) is True


@pytest.mark.asyncio
async def test_answer_registry_wait_times_out():
    reg = AnswerRegistry()
    reg.register("u1")
    assert await reg.wait("u1", timeout=0.01) is False


@pytest.mark.asyncio
async def test_answer_registry_resolve_unknown_is_noop():
    reg = AnswerRegistry()
    # 未登録 uuid の resolve は例外を投げない。
    reg.resolve("nope")


# ===========================================================================
# MediaEventRouter → AnswerRegistry
# ===========================================================================


@pytest.mark.asyncio
async def test_router_resolves_answer_registry_on_channel_answer():
    sess_reg = SessionRegistry()
    ans_reg = AnswerRegistry()
    fut = ans_reg.register("u-answer")
    router = MediaEventRouter(sess_reg, answer_registry=ans_reg)
    await router.handle({"Event-Name": "CHANNEL_ANSWER", "Unique-ID": "u-answer"})
    assert await asyncio.wait_for(fut, timeout=1) is True


@pytest.mark.asyncio
async def test_router_without_answer_registry_is_backward_compatible():
    # answer_registry を渡さない従来構成でも CHANNEL_ANSWER 処理が例外を投げない。
    router = MediaEventRouter(SessionRegistry())
    await router.handle({"Event-Name": "CHANNEL_ANSWER", "Unique-ID": "x"})


# ===========================================================================
# OutboundCallService.dial
# ===========================================================================


class _FakeTrunk:
    def __init__(self, name: str, caller_id: str = "", enabled: bool = True) -> None:
        self.name = name
        self.caller_id = caller_id
        self.enabled = enabled
        self.id = 1


def _make_service(esl, trunks, *, sip_domain="millicall.local", answer_reg=None):
    from millicall.mcp_server.outbound import OutboundCallService

    ans = answer_reg or AnswerRegistry()

    async def _fetch_enabled_trunks():
        return [t for t in trunks if t.enabled]

    return OutboundCallService(
        esl=esl,
        answer_registry=ans,
        sip_domain=sip_domain,
        fetch_enabled_trunks=_fetch_enabled_trunks,
        uuid_factory=lambda: "fixed-uuid",
    ), ans


@pytest.mark.asyncio
async def test_resolve_target_external_uses_first_enabled_trunk():
    esl = _FakeEsl()
    svc, _ = _make_service(esl, [_FakeTrunk("main", caller_id="0312345678")])
    dest, cid = await svc._resolve_target("0901234567", "", "")
    assert dest == "sofia/gateway/main/0901234567"
    # caller_id 未指定 → トランク caller_id を継承。
    assert cid == "0312345678"


@pytest.mark.asyncio
async def test_resolve_target_explicit_trunk_and_caller_id():
    esl = _FakeEsl()
    svc, _ = _make_service(esl, [_FakeTrunk("main", caller_id="0311111111")])
    dest, cid = await svc._resolve_target("0901234567", "0399999999", "main")
    assert dest == "sofia/gateway/main/0901234567"
    assert cid == "0399999999"  # 明示 caller_id 優先


@pytest.mark.asyncio
async def test_resolve_target_186_prefix_treated_as_external():
    esl = _FakeEsl()
    svc, _ = _make_service(esl, [_FakeTrunk("main", caller_id="0312345678")])
    dest, _cid = await svc._resolve_target("1860901234567", "", "")
    assert dest == "sofia/gateway/main/1860901234567"


@pytest.mark.asyncio
async def test_resolve_target_extension_uses_sip_domain():
    esl = _FakeEsl()
    svc, _ = _make_service(esl, [_FakeTrunk("main")])
    dest, _cid = await svc._resolve_target("800", "", "")
    assert dest == "user/800@millicall.local"


@pytest.mark.asyncio
async def test_resolve_target_no_trunk_raises():
    esl = _FakeEsl()
    svc, _ = _make_service(esl, [])  # enabled トランクなし
    with pytest.raises(ValueError, match="トランク"):
        await svc._resolve_target("0901234567", "", "")


# --- ESL コマンドインジェクション対策（HIGH 指摘の回帰） --------------------
@pytest.mark.asyncio
async def test_resolve_target_rejects_injection_in_phone_number():
    esl = _FakeEsl()
    svc, _ = _make_service(esl, [_FakeTrunk("main", caller_id="0312345678")])
    for bad in ["09012 &originate", "090\n1234", "090,x=1", "090}&park", "09012'"]:
        with pytest.raises(ValueError, match="phone_number"):
            await svc._resolve_target(bad, "", "")


@pytest.mark.asyncio
async def test_resolve_target_rejects_injection_in_caller_id():
    esl = _FakeEsl()
    svc, _ = _make_service(esl, [_FakeTrunk("main")])
    with pytest.raises(ValueError, match="caller_id"):
        await svc._resolve_target("0901234567", "03 &sleep", "main")


@pytest.mark.asyncio
async def test_resolve_target_rejects_unknown_trunk():
    esl = _FakeEsl()
    svc, _ = _make_service(esl, [_FakeTrunk("main")])
    with pytest.raises(ValueError, match="unknown trunk"):
        await svc._resolve_target("0901234567", "", "evil}&park")


@pytest.mark.asyncio
async def test_dial_issues_originate_park_and_returns_call_uuid_on_answer():
    esl = _FakeEsl()
    svc, ans = _make_service(esl, [_FakeTrunk("main", caller_id="0312345678")])

    async def _answer_soon():
        await asyncio.sleep(0)
        ans.resolve("fixed-uuid")

    asyncio.get_running_loop().create_task(_answer_soon())
    result = await svc.dial("0901234567", "", "", timeout=1)
    assert result.call_uuid == "fixed-uuid"
    assert result.state == "Up"

    cmd = esl.cmds[-1]
    assert cmd.startswith("originate {")
    assert cmd.endswith("sofia/gateway/main/0901234567 &park")
    assert "origination_uuid=fixed-uuid" in cmd
    assert "origination_caller_id_number=0312345678" in cmd
    assert "verbose_events=true" in cmd
    # 手動系は AI 会話用の audio_stream 自動起動と衝突させない。
    assert "millicall_ai_agent" not in cmd


@pytest.mark.asyncio
async def test_dial_uses_bgapi_for_originate():
    esl = _FakeEsl()
    svc, ans = _make_service(esl, [_FakeTrunk("main")])
    asyncio.get_running_loop().create_task(_delayed_resolve(ans, "fixed-uuid"))
    await svc.dial("0901234567", "", "", timeout=1)
    # originate は bgapi 経由（api ではない）。
    assert any(c.startswith("originate ") for c in esl.cmds)
    assert not esl.api_cmds


@pytest.mark.asyncio
async def test_dial_timeout_raises_dial_timeout_with_uuid():
    from millicall.mcp_server.outbound import DialTimeout

    esl = _FakeEsl()
    svc, _ = _make_service(esl, [_FakeTrunk("main")])
    with pytest.raises(DialTimeout) as excinfo:
        await svc.dial("0901234567", "", "", timeout=0.01)
    assert excinfo.value.call_uuid == "fixed-uuid"


@pytest.mark.asyncio
async def test_dial_explicit_caller_id_reflected_in_origination_var():
    esl = _FakeEsl()
    svc, ans = _make_service(esl, [_FakeTrunk("main", caller_id="0311111111")])
    asyncio.get_running_loop().create_task(_delayed_resolve(ans, "fixed-uuid"))
    await svc.dial("0901234567", "0399999999", "", timeout=1)
    assert "origination_caller_id_number=0399999999" in esl.cmds[-1]


async def _delayed_resolve(ans, uuid):
    await asyncio.sleep(0)
    ans.resolve(uuid)


# ===========================================================================
# CallPrimitives (say / listen / say_and_listen)
# ===========================================================================


class _FakeCallControl:
    def __init__(self) -> None:
        self.played: list[str] = []
        self.played_bytes: list[bytes] = []

    async def play_file(self, path: str) -> None:
        self.played.append(path)
        # ファイルはこの時点で実在する（再生後にサービスが削除するため、
        # 中身を検証したいテストは再生時に読み取る）。
        self.played_bytes.append(Path(path).read_bytes())


class _FakeTTS:
    def __init__(self, pcm: bytes) -> None:
        self.pcm = pcm
        self.texts: list[str] = []

    async def synthesize(self, text: str) -> bytes:
        self.texts.append(text)
        return self.pcm


class _FakeSTTSession:
    def __init__(self, parent) -> None:
        self._parent = parent
        self.fed = bytearray()
        self.finished = False

    async def feed(self, pcm: bytes) -> None:
        self.fed.extend(pcm)

    async def finish(self) -> str:
        self.finished = True
        return self._parent.result


class _FakeSTT:
    def __init__(self, result: str) -> None:
        self.result = result
        self.sessions: list[_FakeSTTSession] = []

    def open_session(self):
        s = _FakeSTTSession(self)
        self.sessions.append(s)
        return s


def _make_primitives(esl, call_control, tts, stt, tts_dir, *, recorded_wav=None):
    from millicall.mcp_server.primitives import CallPrimitives

    async def _read_recording(path):
        return recorded_wav if recorded_wav is not None else _wav_bytes()

    slept: list[float] = []

    async def _sleep(secs):
        slept.append(secs)

    prim = CallPrimitives(
        esl=esl,
        call_uuid="fixed-uuid",
        call_control=call_control,
        tts=tts,
        stt=stt,
        tts_dir=tts_dir,
        sleep=_sleep,
        read_recording=_read_recording,
    )
    return prim, slept


@pytest.mark.asyncio
async def test_say_synthesizes_and_plays(tmp_path):
    esl = _FakeEsl()
    cc = _FakeCallControl()
    tts = _FakeTTS(b"\x00\x00" * 8000)  # 1 秒分 (8000 サンプル * 2 byte)
    prim, _ = _make_primitives(esl, cc, tts, _FakeSTT(""), tmp_path)
    duration = await prim.say("こんにちは")
    assert tts.texts == ["こんにちは"]
    assert len(cc.played) == 1
    assert cc.played[0].endswith(".wav")
    assert duration == pytest.approx(1.0, abs=0.01)


@pytest.mark.asyncio
async def test_say_writes_valid_wav(tmp_path):
    esl = _FakeEsl()
    cc = _FakeCallControl()
    tts = _FakeTTS(b"\x01\x02" * 400)
    prim, _ = _make_primitives(esl, cc, tts, _FakeSTT(""), tmp_path)
    await prim.say("test")
    # 再生時に読み取ったバイト列が有効な WAV である。
    with wave.open(BytesIO(cc.played_bytes[0]), "rb") as w:
        assert w.getframerate() == 8000
        assert w.getnchannels() == 1


@pytest.mark.asyncio
async def test_listen_records_then_transcribes_and_stops(tmp_path):
    esl = _FakeEsl()
    stt = _FakeSTT("もしもし")
    prim, slept = _make_primitives(esl, _FakeCallControl(), _FakeTTS(b""), stt, tmp_path)
    text = await prim.listen(max_seconds=15)
    assert text == "もしもし"
    # 録音開始 → 停止の順で uuid_record が発行される。
    start = [c for c in esl.cmds if c.startswith("uuid_record fixed-uuid start")]
    stop = [c for c in esl.cmds if c.startswith("uuid_record fixed-uuid stop")]
    assert len(start) == 1
    assert len(stop) == 1
    assert "15" in start[0]  # max_seconds が limit に反映
    # 実時間 sleep を回避し、max_seconds を尊重して待つ。
    assert slept == [15]
    # STT セッションが finish されている。
    assert stt.sessions[0].finished is True


@pytest.mark.asyncio
async def test_listen_empty_recording_returns_empty(tmp_path):
    esl = _FakeEsl()
    stt = _FakeSTT("")  # 無音 → STT 空文字
    prim, _ = _make_primitives(
        esl, _FakeCallControl(), _FakeTTS(b""), stt, tmp_path, recorded_wav=_wav_bytes(b"")
    )
    text = await prim.listen(max_seconds=5)
    assert text == ""


@pytest.mark.asyncio
async def test_say_and_listen_combines_say_then_listen(tmp_path):
    esl = _FakeEsl()
    cc = _FakeCallControl()
    tts = _FakeTTS(b"\x00\x00" * 400)
    stt = _FakeSTT("はい、注文をどうぞ")
    prim, _ = _make_primitives(esl, cc, tts, stt, tmp_path)
    said, heard = await prim.say_and_listen("ご注文を承ります", max_seconds=15)
    assert said == "ご注文を承ります"
    assert heard == "はい、注文をどうぞ"
    # say が先（play_file）、listen が後（uuid_record）。
    assert cc.played  # said
    assert any(c.startswith("uuid_record fixed-uuid start") for c in esl.cmds)


# ===========================================================================
# resolve_default_providers（裁定#1: 既定 MCP エージェント / プロバイダ解決）
# ===========================================================================


class _Secrets:
    master_key = "test-master-key"


async def _make_db():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from millicall.db import Base
    from millicall.models import AiAgent, Provider

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as db:
        db.add_all(
            [
                Provider(id=10, name="llm1", type="llm", kind="openai_compatible", config_json="{}"),
                Provider(id=20, name="tts1", type="tts", kind="voicevox", config_json="{}"),
                Provider(id=30, name="stt1", type="stt", kind="google_stt", config_json="{}"),
            ]
        )
        db.add_all(
            [
                AiAgent(
                    id=5,
                    name="agent-b",
                    system_prompt="p",
                    greeting="g",
                    llm_provider_id=10,
                    tts_provider_id=20,
                    stt_provider_id=30,
                    enabled=True,
                ),
                AiAgent(
                    id=3,
                    name="agent-a",
                    system_prompt="p",
                    greeting="g",
                    llm_provider_id=10,
                    tts_provider_id=20,
                    stt_provider_id=30,
                    enabled=True,
                ),
                AiAgent(
                    id=1,
                    name="agent-disabled",
                    system_prompt="p",
                    greeting="g",
                    llm_provider_id=10,
                    tts_provider_id=20,
                    stt_provider_id=30,
                    enabled=False,
                ),
            ]
        )
        await db.commit()
    return sm, engine


@pytest.mark.asyncio
async def test_resolve_default_providers_uses_min_enabled_agent_id_when_none():
    from millicall.mcp_server.outbound import resolve_default_providers

    sm, engine = await _make_db()
    try:
        resolved = await resolve_default_providers(sm, _Secrets(), None)
        # 無効な id=1 を除いた enabled 最小 id=3 が選ばれる。
        assert resolved.agent.id == 3
        assert resolved.tts is not None
        assert resolved.stt is not None
        assert resolved.llm is not None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_resolve_default_providers_respects_explicit_agent_id():
    from millicall.mcp_server.outbound import resolve_default_providers

    sm, engine = await _make_db()
    try:
        resolved = await resolve_default_providers(sm, _Secrets(), 5)
        assert resolved.agent.id == 5
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_resolve_default_providers_no_agent_raises():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from millicall.db import Base
    from millicall.mcp_server.outbound import resolve_default_providers

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    try:
        with pytest.raises(ValueError, match="エージェント"):
            await resolve_default_providers(sm, _Secrets(), None)
    finally:
        await engine.dispose()
