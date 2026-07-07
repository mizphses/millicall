"""Task 5: 音声/TTS 系ノードハンドラ（play_audio / transfer / voicemail / human_escalation）TDD.

テスト方針:
  - ChannelContext は bare インスタンス（ESL 接続なし）
  - call_control / primitives は MagicMock / AsyncMock で差し替え
  - フェイク primitives で say/record の呼び出しシーケンスを検証する
  - CallPrimitives.record() の bgapi コマンドも同ファイルで検証
"""

from __future__ import annotations

import wave
from io import BytesIO
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from millicall.workflows.context import ChannelContext

if TYPE_CHECKING:
    from pathlib import Path

# TDD: このモジュールをインポートする。初回実行は ImportError で落ちる。
from millicall.workflows.handlers.audio import (
    handle_human_escalation,
    handle_play_audio,
    handle_transfer,
    handle_voicemail,
)

# --------------------------------------------------------------------------- #
# ヘルパ
# --------------------------------------------------------------------------- #


def make_ctx(variables: dict | None = None) -> ChannelContext:
    ctx = ChannelContext(uuid="test-uuid")
    if variables:
        for k, v in variables.items():
            ctx.set_var(k, v)
    return ctx


def make_fake_call_control() -> MagicMock:
    cc = MagicMock()
    cc.play_file = AsyncMock()
    cc.transfer = AsyncMock()
    cc.hangup = AsyncMock()
    return cc


def make_fake_primitives() -> MagicMock:
    p = MagicMock()
    p.say = AsyncMock()
    p.record = AsyncMock()
    return p


# ---- ノードファクトリ ---- #


def make_play_audio_node(
    tts_text: str = "こんにちは",
    file_path: str = "",
    tts_provider_id: int | None = None,
):
    from millicall.workflows.nodes import PlayAudioConfig, PlayAudioNode

    return PlayAudioNode(
        id="pa1",
        type="play_audio",
        config=PlayAudioConfig(
            tts_text=tts_text,
            file_path=file_path,
            tts_provider_id=tts_provider_id,
        ),
    )


def make_transfer_node(destination: str = "1234") -> object:
    from millicall.workflows.nodes import TransferConfig, TransferNode

    return TransferNode(
        id="tr1",
        type="transfer",
        config=TransferConfig(destination=destination),
    )


def make_voicemail_node(
    mailbox: str = "box1",
    greeting_text: str = "",
    max_seconds: int = 120,
) -> object:
    from millicall.workflows.nodes import VoicemailConfig, VoicemailNode

    return VoicemailNode(
        id="vm1",
        type="voicemail",
        config=VoicemailConfig(
            mailbox=mailbox,
            greeting_text=greeting_text,
            max_seconds=max_seconds,
        ),
    )


def make_human_escalation_node(
    destination: str = "5000",
    announcement_text: str = "",
    summary_to_agent: bool = True,
) -> object:
    from millicall.workflows.nodes import HumanEscalationConfig, HumanEscalationNode

    return HumanEscalationNode(
        id="he1",
        type="human_escalation",
        config=HumanEscalationConfig(
            destination=destination,
            announcement_text=announcement_text,
            summary_to_agent=summary_to_agent,
        ),
    )


# =========================================================================== #
# play_audio
# =========================================================================== #


@pytest.mark.asyncio
async def test_play_audio_tts_calls_say() -> None:
    """TTS テキストが primitives.say() に渡される。"""
    ctx = make_ctx()
    ctx.primitives = make_fake_primitives()
    node = make_play_audio_node(tts_text="こんにちは")

    result = await handle_play_audio(node, ctx)

    assert result is None  # 単一出力ノード → "out" 既定遷移
    ctx.primitives.say.assert_awaited_once_with("こんにちは")


@pytest.mark.asyncio
async def test_play_audio_template_expanded_before_say() -> None:
    """{{var}} がテンプレート展開されてから say() が呼ばれる。"""
    ctx = make_ctx({"name": "田中"})
    ctx.primitives = make_fake_primitives()
    node = make_play_audio_node(tts_text="{{name}}様、お待ちください")

    await handle_play_audio(node, ctx)

    ctx.primitives.say.assert_awaited_once_with("田中様、お待ちください")


@pytest.mark.asyncio
async def test_play_audio_file_path_calls_play_file_not_say() -> None:
    """file_path が設定されている場合は play_file() を使い say() は呼ばない。"""
    ctx = make_ctx()
    ctx.call_control = make_fake_call_control()
    ctx.primitives = make_fake_primitives()
    node = make_play_audio_node(tts_text="dummy", file_path="/audio/greeting.wav")

    result = await handle_play_audio(node, ctx)

    assert result is None
    ctx.call_control.play_file.assert_awaited_once_with("/audio/greeting.wav")
    ctx.primitives.say.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_path",
    [
        "/audio/x.wav; uuid_kill uuid",  # ESL コマンド連結
        "/audio/x.wav\nuuid_kill uuid",  # 改行注入
        "/audio/x.wav && rm -rf /",       # シェルメタ
        "/audio/`id`.wav",                # バッククォート
        "/audio/x |tee.wav",              # 空白・パイプ
    ],
)
async def test_play_audio_rejects_injection_file_path(bad_path: str) -> None:
    """file_path が allowlist 外なら play_file を呼ばず再生をスキップする（ESL注入対策）。"""
    ctx = make_ctx()
    ctx.call_control = make_fake_call_control()
    ctx.primitives = make_fake_primitives()
    node = make_play_audio_node(tts_text="dummy", file_path=bad_path)

    result = await handle_play_audio(node, ctx)

    assert result is None
    ctx.call_control.play_file.assert_not_called()
    ctx.primitives.say.assert_not_called()


@pytest.mark.asyncio
async def test_play_audio_no_primitives_no_crash() -> None:
    """ctx.primitives が None でもクラッシュしない（graceful skip）。"""
    ctx = make_ctx()  # primitives=None
    node = make_play_audio_node(tts_text="テスト")

    result = await handle_play_audio(node, ctx)

    assert result is None


@pytest.mark.asyncio
async def test_play_audio_no_call_control_no_crash() -> None:
    """file_path あり・ctx.call_control が None でもクラッシュしない。"""
    ctx = make_ctx()  # call_control=None
    node = make_play_audio_node(tts_text="dummy", file_path="/audio/file.wav")

    result = await handle_play_audio(node, ctx)

    assert result is None


def test_play_audio_registered_in_handlers() -> None:
    """play_audio ハンドラが HANDLERS に登録されている。"""
    from millicall.workflows.executor import HANDLERS

    assert "play_audio" in HANDLERS


# =========================================================================== #
# transfer
# =========================================================================== #


@pytest.mark.asyncio
async def test_transfer_calls_call_control_transfer() -> None:
    """call_control.transfer() が destination で呼ばれる。"""
    ctx = make_ctx()
    ctx.call_control = make_fake_call_control()
    node = make_transfer_node(destination="9001")

    result = await handle_transfer(node, ctx)

    assert result is None  # terminal ノードは None を返す
    ctx.call_control.transfer.assert_awaited_once_with("9001")


@pytest.mark.asyncio
async def test_transfer_no_call_control_no_crash() -> None:
    """ctx.call_control が None でもクラッシュしない。"""
    ctx = make_ctx()
    node = make_transfer_node(destination="9001")

    result = await handle_transfer(node, ctx)

    assert result is None


def test_transfer_registered_in_handlers() -> None:
    """transfer ハンドラが HANDLERS に登録されている。"""
    from millicall.workflows.executor import HANDLERS

    assert "transfer" in HANDLERS


# =========================================================================== #
# voicemail
# =========================================================================== #


@pytest.mark.asyncio
async def test_voicemail_greeting_then_record(tmp_path: Path) -> None:
    """greeting 再生 → 録音 のシーケンスを検証する。"""
    ctx = make_ctx()
    ctx.primitives = make_fake_primitives()
    ctx.tts_dir = tmp_path / "tts_cache"
    node = make_voicemail_node(mailbox="main", greeting_text="ボイスメールです", max_seconds=60)

    result = await handle_voicemail(node, ctx)

    assert result is None  # terminal
    ctx.primitives.say.assert_awaited_once_with("ボイスメールです")
    ctx.primitives.record.assert_awaited_once()
    # record の第 2 引数は max_seconds
    record_args = ctx.primitives.record.call_args[0]
    assert record_args[1] == 60


@pytest.mark.asyncio
async def test_voicemail_no_greeting_skips_say(tmp_path: Path) -> None:
    """greeting_text が空の場合 say() は呼ばれない。"""
    ctx = make_ctx()
    ctx.primitives = make_fake_primitives()
    ctx.tts_dir = tmp_path / "tts_cache"
    node = make_voicemail_node(mailbox="box1", greeting_text="", max_seconds=30)

    await handle_voicemail(node, ctx)

    ctx.primitives.say.assert_not_called()
    ctx.primitives.record.assert_awaited_once()


@pytest.mark.asyncio
async def test_voicemail_stores_path_in_variable(tmp_path: Path) -> None:
    """録音パスが voicemail_path 変数に格納され、mailbox 名を含む。"""
    ctx = make_ctx()
    ctx.primitives = make_fake_primitives()
    ctx.tts_dir = tmp_path / "tts_cache"
    node = make_voicemail_node(mailbox="inbox", greeting_text="")

    await handle_voicemail(node, ctx)

    stored_path = ctx.get_var("voicemail_path")
    assert stored_path != ""
    assert "inbox" in stored_path  # mailbox 名が含まれる


@pytest.mark.asyncio
async def test_voicemail_no_primitives_no_crash() -> None:
    """ctx.primitives が None でもクラッシュせず voicemail_path が設定される。"""
    ctx = make_ctx()
    # primitives=None → 録音はスキップ
    node = make_voicemail_node(mailbox="box1")

    result = await handle_voicemail(node, ctx)

    assert result is None
    # primitives なし: voicemail_path は空
    assert ctx.get_var("voicemail_path") == ""


@pytest.mark.asyncio
async def test_voicemail_template_expanded_in_greeting(tmp_path: Path) -> None:
    """greeting_text の {{var}} が展開されてから say() が呼ばれる。"""
    ctx = make_ctx({"caller": "山田"})
    ctx.primitives = make_fake_primitives()
    ctx.tts_dir = tmp_path / "tts_cache"
    node = make_voicemail_node(mailbox="box1", greeting_text="{{caller}}様のボイスメールです")

    await handle_voicemail(node, ctx)

    ctx.primitives.say.assert_awaited_once_with("山田様のボイスメールです")


@pytest.mark.asyncio
async def test_voicemail_record_path_passed_as_first_arg(tmp_path: Path) -> None:
    """record() の第 1 引数がパス文字列であること。"""
    ctx = make_ctx()
    ctx.primitives = make_fake_primitives()
    ctx.tts_dir = tmp_path / "tts_cache"
    node = make_voicemail_node(mailbox="testbox")

    await handle_voicemail(node, ctx)

    record_args = ctx.primitives.record.call_args[0]
    path_arg = record_args[0]
    # パス文字列であることを確認（.wav 拡張子 or str 型）
    assert isinstance(path_arg, str)
    assert len(path_arg) > 0


def test_voicemail_registered_in_handlers() -> None:
    """voicemail ハンドラが HANDLERS に登録されている。"""
    from millicall.workflows.executor import HANDLERS

    assert "voicemail" in HANDLERS


# =========================================================================== #
# human_escalation
# =========================================================================== #


@pytest.mark.asyncio
async def test_human_escalation_announcement_then_transfer() -> None:
    """announcement 再生 → 転送 のシーケンスを検証する。"""
    ctx = make_ctx()
    ctx.primitives = make_fake_primitives()
    ctx.call_control = make_fake_call_control()
    node = make_human_escalation_node(
        destination="operator",
        announcement_text="担当者に転送します",
    )

    result = await handle_human_escalation(node, ctx)

    assert result is None  # terminal
    ctx.primitives.say.assert_awaited_once_with("担当者に転送します")
    ctx.call_control.transfer.assert_awaited_once_with("operator")


@pytest.mark.asyncio
async def test_human_escalation_no_announcement_skips_say() -> None:
    """announcement_text が空の場合 say() は呼ばれない。"""
    ctx = make_ctx()
    ctx.primitives = make_fake_primitives()
    ctx.call_control = make_fake_call_control()
    node = make_human_escalation_node(destination="5000", announcement_text="")

    await handle_human_escalation(node, ctx)

    ctx.primitives.say.assert_not_called()
    ctx.call_control.transfer.assert_awaited_once_with("5000")


@pytest.mark.asyncio
async def test_human_escalation_template_expanded_in_announcement() -> None:
    """announcement_text の {{var}} が展開される。"""
    ctx = make_ctx({"dept": "技術部"})
    ctx.primitives = make_fake_primitives()
    ctx.call_control = make_fake_call_control()
    node = make_human_escalation_node(
        destination="tech",
        announcement_text="{{dept}}に接続します",
    )

    await handle_human_escalation(node, ctx)

    ctx.primitives.say.assert_awaited_once_with("技術部に接続します")


@pytest.mark.asyncio
async def test_human_escalation_no_primitives_no_crash() -> None:
    """ctx.primitives が None でもクラッシュしない（アナウンスはスキップ）。"""
    ctx = make_ctx()
    ctx.call_control = make_fake_call_control()
    node = make_human_escalation_node(destination="9999", announcement_text="テスト")

    result = await handle_human_escalation(node, ctx)

    assert result is None
    ctx.call_control.transfer.assert_awaited_once_with("9999")


@pytest.mark.asyncio
async def test_human_escalation_no_call_control_no_crash() -> None:
    """ctx.call_control が None でもクラッシュしない（転送はスキップ）。"""
    ctx = make_ctx()
    ctx.primitives = make_fake_primitives()
    node = make_human_escalation_node(destination="9999", announcement_text="テスト")

    result = await handle_human_escalation(node, ctx)

    assert result is None
    ctx.primitives.say.assert_awaited_once()


def test_human_escalation_registered_in_handlers() -> None:
    """human_escalation ハンドラが HANDLERS に登録されている。"""
    from millicall.workflows.executor import HANDLERS

    assert "human_escalation" in HANDLERS


# =========================================================================== #
# CallPrimitives.record() — bgapi シーケンス検証
# =========================================================================== #


def _wav_bytes(pcm: bytes = b"\x00\x00" * 800) -> bytes:
    buf = BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(pcm)
    return buf.getvalue()


class _FakeEsl:
    def __init__(self) -> None:
        self.cmds: list[str] = []

    async def bgapi(self, command: str) -> str:
        self.cmds.append(command)
        return "job-uuid"


class _FakeTts:
    async def synthesize(self, text: str) -> bytes:
        return b"\x00\x01" * 100


class _FakeStt:
    class _Sess:
        async def feed(self, pcm: bytes) -> None: ...
        async def finish(self) -> str:
            return "ok"
    def open_session(self) -> _FakeStt._Sess:
        return self._Sess()


@pytest.mark.asyncio
async def test_primitives_record_issues_start_stop_bgapi(tmp_path: Path) -> None:
    """CallPrimitives.record() が uuid_record start/stop を発行し、パスを返す。"""
    from millicall.mcp_server.primitives import CallPrimitives

    esl = _FakeEsl()
    slept: list[float] = []

    async def fake_sleep(secs: float) -> None:
        slept.append(secs)

    cc = MagicMock()
    cc.play_file = AsyncMock()

    prim = CallPrimitives(
        esl=esl,
        call_uuid="uuid-1",
        call_control=cc,
        tts=_FakeTts(),
        stt=_FakeStt(),
        tts_dir=tmp_path,
        sleep=fake_sleep,
    )

    recording_path = str(tmp_path / "vm_test.wav")
    returned = await prim.record(recording_path, max_seconds=5)

    # start/stop の bgapi が発行されていること
    assert any(f"uuid_record uuid-1 start {recording_path} 5" in c for c in esl.cmds), (
        f"start が見つからない。cmds={esl.cmds}"
    )
    assert any(f"uuid_record uuid-1 stop {recording_path}" in c for c in esl.cmds), (
        f"stop が見つからない。cmds={esl.cmds}"
    )
    # sleep が max_seconds 秒呼ばれていること
    assert slept == [5.0]
    # パスが返ること
    assert returned == recording_path


@pytest.mark.asyncio
async def test_primitives_record_stop_issued_even_on_sleep_error(tmp_path: Path) -> None:
    """sleep が例外を投げても stop コマンドが発行される（finally 保証）。"""
    from millicall.mcp_server.primitives import CallPrimitives

    esl = _FakeEsl()

    async def bad_sleep(secs: float) -> None:
        raise RuntimeError("sleep interrupted")

    cc = MagicMock()
    cc.play_file = AsyncMock()

    prim = CallPrimitives(
        esl=esl,
        call_uuid="uuid-2",
        call_control=cc,
        tts=_FakeTts(),
        stt=_FakeStt(),
        tts_dir=tmp_path,
        sleep=bad_sleep,
    )

    recording_path = str(tmp_path / "vm_stop_test.wav")
    with pytest.raises(RuntimeError):
        await prim.record(recording_path, max_seconds=10)

    # 例外が伝播しても stop は呼ばれている
    assert any("stop" in c and recording_path in c for c in esl.cmds), (
        f"stop が呼ばれていない。cmds={esl.cmds}"
    )


def _make_prim(tmp_path: Path) -> object:
    from millicall.mcp_server.primitives import CallPrimitives

    cc = MagicMock()
    cc.play_file = AsyncMock()

    async def fake_sleep(secs: float) -> None: ...

    return CallPrimitives(
        esl=_FakeEsl(),
        call_uuid="uuid-x",
        call_control=cc,
        tts=_FakeTts(),
        stt=_FakeStt(),
        tts_dir=tmp_path,
        sleep=fake_sleep,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_path",
    [
        "/tmp/vm.wav; uuid_kill uuid-x",   # ESL コマンド連結
        "/tmp/vm.wav\nuuid_kill uuid-x",   # 改行注入
        "/tmp/vm.wav && rm -rf /",          # シェルメタ
        "/tmp/vm $(whoami).wav",            # 空白・コマンド置換
        "/tmp/`id`.wav",                    # バッククォート
        "/tmp/vm|tee.wav",                  # パイプ
        "",                                  # 空文字
    ],
)
async def test_primitives_record_rejects_injection_paths(tmp_path: Path, bad_path: str) -> None:
    """record() は allowlist 外のパスを ValueError で拒否し bgapi を発行しない。"""
    prim = _make_prim(tmp_path)
    with pytest.raises(ValueError):
        await prim.record(bad_path, max_seconds=5)
    assert prim._esl.cmds == []  # type: ignore[attr-defined]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_dur", [0, -1, 3.5, True, "5"])
async def test_primitives_record_rejects_bad_duration(tmp_path: Path, bad_dur: object) -> None:
    """record() は非正/非整数の max_seconds を拒否する。"""
    prim = _make_prim(tmp_path)
    with pytest.raises(ValueError):
        await prim.record(str(tmp_path / "vm.wav"), max_seconds=bad_dur)  # type: ignore[arg-type]
    assert prim._esl.cmds == []  # type: ignore[attr-defined]


def test_safe_mailbox_strips_metacharacters() -> None:
    """_safe_mailbox が ESL/パスメタ文字を除去し安全な slug にする。"""
    from millicall.workflows.handlers.audio import _safe_mailbox

    assert _safe_mailbox("box; rm -rf /") == "box__rm_-rf__"
    assert _safe_mailbox("../../etc/passwd") == "______etc_passwd"
    assert _safe_mailbox("") == "default"
    assert _safe_mailbox("normal_box-1") == "normal_box-1"
