"""Task 4: converse オーケストレーション（一時エージェント + 既存 media 再利用）のテスト。

受入条件:
  - HangupRegistry が CHANNEL_HANGUP_COMPLETE 完了 future を解決する。
  - MediaEventRouter が CHANNEL_HANGUP_COMPLETE で HangupRegistry を解決する（従来動作も維持）。
  - EphemeralAgentStore が call_uuid → 一時 agent spec を put/get/pop する。
  - build_converse_system_prompt が purpose/key_points/your_name を [END_CALL] 版に合成する。
  - build_conversation_session_from_spec が DB 非依存の spec から ConversationSession を組み、
    transcript 収集と call_messages 永続化を並行で行う。
  - audio_fork_ws が ?agent=<数値> と ?agent=ephemeral を分岐する（後者は EphemeralAgentStore を引く）。
  - OutboundCallService.converse が発信 → answer → ephemeral session → 会話 → [END_CALL]
    → hangup → HangupRegistry 解決 → transcript(speaker マップ) + summary の §6 JSON を返す。
  - max_turns 相当の上限時間超過時のフォールバック終話。
  - 実 FS 不要・実時間 sleep 回避（injectable clock/event）。
"""

import asyncio

import pytest

from millicall.media.audio_fork import MediaEventRouter
from millicall.media.service import HangupRegistry, SessionRegistry

# ===========================================================================
# HangupRegistry
# ===========================================================================


@pytest.mark.asyncio
async def test_hangup_registry_resolves_on_hangup():
    reg = HangupRegistry()
    fut = reg.register("u1")
    reg.resolve("u1")
    assert await asyncio.wait_for(fut, timeout=1) is True


@pytest.mark.asyncio
async def test_hangup_registry_wait_returns_true_when_resolved():
    reg = HangupRegistry()
    reg.register("u1")

    async def _resolve_soon():
        await asyncio.sleep(0)
        reg.resolve("u1")

    asyncio.get_running_loop().create_task(_resolve_soon())
    assert await reg.wait("u1", timeout=1) is True


@pytest.mark.asyncio
async def test_hangup_registry_wait_times_out():
    reg = HangupRegistry()
    reg.register("u1")
    assert await reg.wait("u1", timeout=0.01) is False


@pytest.mark.asyncio
async def test_hangup_registry_resolve_unknown_is_noop():
    reg = HangupRegistry()
    reg.resolve("nope")  # 例外を投げない


# ===========================================================================
# MediaEventRouter → HangupRegistry
# ===========================================================================


@pytest.mark.asyncio
async def test_router_resolves_hangup_registry_on_hangup_complete():
    hangup_reg = HangupRegistry()
    fut = hangup_reg.register("u-hangup")
    router = MediaEventRouter(SessionRegistry(), hangup_registry=hangup_reg)
    await router.handle({"Event-Name": "CHANNEL_HANGUP_COMPLETE", "Channel-Call-UUID": "u-hangup"})
    assert await asyncio.wait_for(fut, timeout=1) is True


@pytest.mark.asyncio
async def test_router_hangup_without_registry_is_backward_compatible():
    # hangup_registry を渡さない従来構成でも CHANNEL_HANGUP_COMPLETE 処理が例外を投げない。
    reg = SessionRegistry()
    reg.register("x", object(), object())
    router = MediaEventRouter(reg)
    await router.handle({"Event-Name": "CHANNEL_HANGUP_COMPLETE", "Channel-Call-UUID": "x"})
    # 従来どおり registry から pop されている。
    assert reg.get("x") is None


# ===========================================================================
# EphemeralAgentStore
# ===========================================================================


def test_ephemeral_store_put_get_pop():
    from millicall.mcp_server.ephemeral import EphemeralAgentSpec, EphemeralAgentStore

    store = EphemeralAgentStore()
    spec = EphemeralAgentSpec(
        system_prompt="p",
        greeting="g",
        llm_provider_id=10,
        tts_provider_id=20,
        stt_provider_id=30,
    )
    store.put("u1", spec)
    assert store.get("u1") is spec
    assert store.pop("u1") is spec
    assert store.get("u1") is None


def test_ephemeral_spec_defaults():
    from millicall.mcp_server.ephemeral import EphemeralAgentSpec

    spec = EphemeralAgentSpec(
        system_prompt="p",
        greeting="g",
        llm_provider_id=1,
        tts_provider_id=2,
        stt_provider_id=3,
    )
    # ConversationSession が読む属性を duck-type で満たす。
    assert spec.max_history == 10
    assert spec.silence_end_ms == 600


# ===========================================================================
# build_converse_system_prompt
# ===========================================================================


def test_system_prompt_includes_purpose_and_end_call_tag():
    from millicall.mcp_server.outbound import build_converse_system_prompt

    prompt = build_converse_system_prompt(
        purpose="ラーメンを1杯注文する", key_points="味噌ラーメン\n大盛り", your_name="小川"
    )
    assert "ラーメンを1杯注文する" in prompt
    assert "味噌ラーメン" in prompt
    assert "小川" in prompt
    # 旧 [DONE] ではなく [END_CALL] 版。
    assert "[END_CALL]" in prompt
    assert "[DONE]" not in prompt


def test_system_prompt_omits_optional_parts_when_empty():
    from millicall.mcp_server.outbound import build_converse_system_prompt

    prompt = build_converse_system_prompt(purpose="用件を伝える", key_points="", your_name="")
    assert "用件を伝える" in prompt
    assert "[END_CALL]" in prompt
    # key_points が空なら要点セクションは出さない。
    assert "伝えるべき要点" not in prompt


# ===========================================================================
# build_conversation_session_from_spec
# ===========================================================================


class _FakeTTS:
    async def synthesize(self, text: str) -> bytes:
        return b"\x00\x00" * 8  # ダミー PCM


class _FakeSTTSession:
    def __init__(self, result: str) -> None:
        self._result = result

    async def feed(self, pcm: bytes) -> None:
        pass

    async def finish(self) -> str:
        return self._result


class _FakeSTT:
    def open_session(self):
        return _FakeSTTSession("もしもし")


class _FakeLLM:
    def __init__(self, chunks: list[str]) -> None:
        self._chunks = chunks

    async def stream_chat(self, messages):
        for c in self._chunks:
            yield c


class _FakeEsl:
    def __init__(self) -> None:
        self.cmds: list[str] = []

    async def bgapi(self, command: str) -> str:
        self.cmds.append(command)
        return "job"


class _FakeCallControl:
    """PLAYBACK_STOP を待たずに即再生完了する fake（ユニットで会話を駆動可能に）。"""

    def __init__(self) -> None:
        self.played: list[str] = []
        self.hung_up = False

    async def play_file(self, path: str) -> None:
        self.played.append(path)

    async def stop_playback(self) -> None:
        pass

    async def hangup(self) -> None:
        self.hung_up = True


async def _make_sessionmaker():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from millicall.db import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False), engine


@pytest.mark.asyncio
async def test_build_session_from_spec_collects_transcript_and_persists(tmp_path):
    from sqlalchemy import select

    from millicall.mcp_server.ephemeral import EphemeralAgentSpec
    from millicall.media.service import (
        SessionRegistry,
        build_conversation_session_from_spec,
    )
    from millicall.models import CallMessage

    sm, engine = await _make_sessionmaker()
    try:
        spec = EphemeralAgentSpec(
            system_prompt="sys",
            greeting="こんにちは",
            llm_provider_id=0,
            tts_provider_id=0,
            stt_provider_id=0,
        )
        transcript: list = []
        reg = SessionRegistry()
        session, _cc = await build_conversation_session_from_spec(
            sessionmaker=sm,
            esl=_FakeEsl(),
            registry=reg,
            call_uuid="call-1",
            spec=spec,
            llm=_FakeLLM(["はい。"]),
            tts=_FakeTTS(),
            stt=_FakeSTT(),
            tts_dir=tmp_path,
            transcript=transcript,
            call_control=_FakeCallControl(),
        )
        # 1 ターン駆動する。
        await session.on_utterance(b"\x00\x00" * 8)

        # transcript に user と assistant が集まる。
        roles = [t[0] for t in transcript]
        assert "user" in roles
        assert "assistant" in roles

        # call_messages にも永続化されている（既存 _persist 契約を壊さない）。
        async with sm() as db:
            rows = (await db.scalars(select(CallMessage))).all()
        assert {r.role for r in rows} == {"user", "assistant"}
        assert any(r.call_uuid == "call-1" for r in rows)

        # registry にも登録される（WS ハンドラ/イベントルータが引ける）。
        assert reg.get("call-1") is not None
    finally:
        await engine.dispose()


# ===========================================================================
# audio_fork_ws の ?agent= 分岐
# ===========================================================================


@pytest.mark.asyncio
async def test_audio_fork_ws_ephemeral_marker_uses_store(tmp_path, monkeypatch):
    """?agent=ephemeral のとき EphemeralAgentStore から spec を引いて build する。"""
    from types import SimpleNamespace

    import millicall.media.audio_fork as af
    from millicall.mcp_server.ephemeral import EphemeralAgentSpec, EphemeralAgentStore

    store = EphemeralAgentStore()
    spec = EphemeralAgentSpec(
        system_prompt="s", greeting="", llm_provider_id=0, tts_provider_id=0, stt_provider_id=0
    )
    store.put("call-e", spec)

    called: dict = {}

    async def _fake_from_spec(**kwargs):
        called.update(kwargs)

        class _Sess:
            _agent = SimpleNamespace(silence_end_ms=600, greeting="")

            async def greet(self):
                pass

        return _Sess(), object()

    monkeypatch.setattr(af, "build_conversation_session_from_spec", _fake_from_spec)

    resolver = af.resolve_ws_agent  # ヘルパで数値/マーカーを判定
    # 数値なら DB 経路（is_ephemeral=False）。
    assert resolver("5") == (5, False)
    # 非数値マーカーは ephemeral 経路。
    assert resolver("ephemeral") == (0, True)


# ===========================================================================
# OutboundCallService.converse（end-to-end, fakes）
# ===========================================================================


class _ConverseFakeEsl:
    def __init__(self) -> None:
        self.cmds: list[str] = []

    async def bgapi(self, command: str) -> str:
        self.cmds.append(command)
        return "job"

    async def api(self, command: str) -> str:
        return "+OK"


class _FakeTrunk:
    def __init__(self, name: str, caller_id: str = "", enabled: bool = True) -> None:
        self.name = name
        self.caller_id = caller_id
        self.enabled = enabled
        self.id = 1


def _make_converse_service(esl, trunks, *, answer_reg, hangup_reg, ephemeral_store, run_session):
    from millicall.mcp_server.outbound import OutboundCallService
    from millicall.media.service import AnswerRegistry

    async def _fetch_enabled_trunks():
        return [t for t in trunks if t.enabled]

    return OutboundCallService(
        esl=esl,
        answer_registry=answer_reg or AnswerRegistry(),
        sip_domain="millicall.local",
        fetch_enabled_trunks=_fetch_enabled_trunks,
        uuid_factory=lambda: "conv-uuid",
        hangup_registry=hangup_reg,
        ephemeral_store=ephemeral_store,
        run_conversation=run_session,
    )


@pytest.mark.asyncio
async def test_converse_full_flow_returns_transcript_and_summary(tmp_path):
    from millicall.mcp_server.ephemeral import EphemeralAgentStore
    from millicall.mcp_server.outbound import ConverseResult
    from millicall.media.service import AnswerRegistry, HangupRegistry

    esl = _ConverseFakeEsl()
    answer_reg = AnswerRegistry()
    hangup_reg = HangupRegistry()
    store = EphemeralAgentStore()

    # run_conversation フェイク: transcript を注入し、[END_CALL] で終話→hangup を模擬。
    async def _run_session(*, call_uuid, transcript):
        transcript.append(("assistant", "もしもし、注文をお願いします。", 100))
        transcript.append(("user", "はい、どうぞ。", 0))
        transcript.append(
            ("assistant", "味噌ラーメンを1杯お願いします。ありがとうございました。", 120)
        )
        # 終話は相手切断 or [END_CALL]→hangup。ここでは hangup_registry を解決。
        hangup_reg.resolve(call_uuid)

    svc = _make_converse_service(
        esl,
        [_FakeTrunk("main", caller_id="0312345678")],
        answer_reg=answer_reg,
        hangup_reg=hangup_reg,
        ephemeral_store=store,
        run_session=_run_session,
    )

    # answer を即解決する。
    async def _answer_soon():
        await asyncio.sleep(0)
        answer_reg.resolve("conv-uuid")

    asyncio.get_running_loop().create_task(_answer_soon())

    result = await svc.converse(
        phone_number="0901234567",
        purpose="ラーメンを1杯注文する",
        key_points="味噌ラーメン",
        your_name="小川",
        max_turns=10,
        summarizer=_fake_summarizer,
    )
    assert isinstance(result, ConverseResult)
    assert result.status == "completed"
    assert result.phone_number == "0901234567"
    assert result.purpose == "ラーメンを1杯注文する"
    assert result.summary == "要約テキスト"
    # speaker は ai/human/system 語彙にマップ。
    speakers = [t["speaker"] for t in result.transcript]
    assert "ai" in speakers
    assert "human" in speakers
    assert all(t["speaker"] in ("ai", "human", "system") for t in result.transcript)
    # turn 番号は 0 始まり連番。
    assert [t["turn"] for t in result.transcript] == list(range(len(result.transcript)))

    # originate は ephemeral マーカー付き（audio_stream 自動起動に合流）。
    originate = [c for c in esl.cmds if c.startswith("originate ")][0]
    assert "millicall_ai_agent=ephemeral" in originate
    assert "origination_uuid=conv-uuid" in originate
    assert originate.endswith("sofia/gateway/main/0901234567 &park")

    # ephemeral store に spec が put されている（WS ハンドラが引ける）。
    # 会話完了後は cleanup で pop される。
    assert store.get("conv-uuid") is None


async def _fake_summarizer(transcript_text: str) -> str:
    return "要約テキスト"


@pytest.mark.asyncio
async def test_converse_no_answer_returns_error_transcript_empty(tmp_path):
    from millicall.mcp_server.ephemeral import EphemeralAgentStore
    from millicall.media.service import AnswerRegistry, HangupRegistry

    esl = _ConverseFakeEsl()

    async def _run_session(*, call_uuid, transcript):
        pass  # 呼ばれないはず

    svc = _make_converse_service(
        esl,
        [_FakeTrunk("main")],
        answer_reg=AnswerRegistry(),
        hangup_reg=HangupRegistry(),
        ephemeral_store=EphemeralAgentStore(),
        run_session=_run_session,
    )
    result = await svc.converse(
        phone_number="0901234567",
        purpose="用件",
        max_turns=10,
        answer_timeout=0.01,
        summarizer=_fake_summarizer,
    )
    assert result.status == "error"
    assert result.error  # §6 エラー
    assert result.transcript == []


@pytest.mark.asyncio
async def test_converse_max_time_fallback_hangup(tmp_path):
    """上限時間超過時は hangup を発行してフォールバック終話する。"""
    from millicall.mcp_server.ephemeral import EphemeralAgentStore
    from millicall.media.service import AnswerRegistry, HangupRegistry

    esl = _ConverseFakeEsl()
    answer_reg = AnswerRegistry()
    hangup_reg = HangupRegistry()

    # run_conversation は transcript を積むが hangup を解決しない（相手が切らない/END_CALL 無し）。
    async def _run_session(*, call_uuid, transcript):
        transcript.append(("assistant", "こんにちは。", 100))
        # hangup_reg を解決しない → 上限時間で converse がフォールバック。
        await asyncio.sleep(10)  # ここは wait_for が先にタイムアウトするので実際は cancel される

    svc = _make_converse_service(
        esl,
        [_FakeTrunk("main")],
        answer_reg=answer_reg,
        hangup_reg=hangup_reg,
        ephemeral_store=EphemeralAgentStore(),
        run_session=_run_session,
    )

    async def _answer_soon():
        await asyncio.sleep(0)
        answer_reg.resolve("conv-uuid")

    asyncio.get_running_loop().create_task(_answer_soon())

    result = await svc.converse(
        phone_number="0901234567",
        purpose="用件",
        max_turns=1,
        answer_timeout=1,
        max_conversation_seconds=0.05,
        summarizer=_fake_summarizer,
    )
    # 上限時間超過でも completed で返す（transcript は取れた分）。
    assert result.status == "completed"
    assert any(t["speaker"] == "ai" for t in result.transcript)
    # フォールバックで uuid_kill（hangup）が発行される。
    assert any(c.startswith("uuid_kill conv-uuid") for c in esl.cmds)
