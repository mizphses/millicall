"""Task 6: DTMF 収集ノードハンドラ（dtmf_input / menu）TDD.

テスト方針:
  - ChannelContext は bare インスタンス（ESL 接続なし）
  - ctx.dtmf はフェイク BoundDtmf（canned digits を返す AsyncMock）で差し替え
  - ctx.primitives / ctx.call_control は AsyncMock で差し替え
  - ハンドラが HANDLERS に登録されていることを同ファイルで確認する
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from millicall.workflows.context import ChannelContext

# TDD: このモジュールをインポートする。
from millicall.workflows.handlers.dtmf import handle_dtmf_input, handle_menu

# --------------------------------------------------------------------------- #
# ヘルパ
# --------------------------------------------------------------------------- #


def make_ctx() -> ChannelContext:
    return ChannelContext(uuid="test-uuid")


def make_fake_primitives() -> MagicMock:
    p = MagicMock()
    p.say = AsyncMock()
    return p


def make_fake_call_control() -> MagicMock:
    cc = MagicMock()
    cc.play_file = AsyncMock()
    return cc


class FakeBoundDtmf:
    """canned digits を返すフェイク BoundDtmf。"""

    def __init__(self, digits: str) -> None:
        self._digits = digits
        self.collect = AsyncMock(return_value=digits)


def make_dtmf_input_node(
    *,
    max_digits: int = 1,
    timeout: int = 5,
    terminator: str = "#",
    prompt_mode: str = "none",
    prompt_text: str = "",
    variable: str = "dtmf_result",
) -> object:
    from millicall.workflows.nodes import DtmfInputConfig, DtmfInputNode

    return DtmfInputNode(
        id="di1",
        type="dtmf_input",
        config=DtmfInputConfig(
            max_digits=max_digits,
            timeout=timeout,
            terminator=terminator,
            prompt_mode=prompt_mode,
            prompt_text=prompt_text,
            variable=variable,
        ),
    )


def make_menu_node(
    *,
    prompt_text: str = "番号を押してください",
    prompt_mode: str = "none",
    timeout: int = 5,
    max_retries: int = 3,
    invalid_text: str = "",
) -> object:
    from millicall.workflows.nodes import MenuConfig, MenuNode

    return MenuNode(
        id="mn1",
        type="menu",
        config=MenuConfig(
            prompt_text=prompt_text,
            prompt_mode=prompt_mode,
            timeout=timeout,
            max_retries=max_retries,
            invalid_text=invalid_text,
        ),
    )


# =========================================================================== #
# dtmf_input — ハンドラ登録確認
# =========================================================================== #


def test_dtmf_input_registered_in_handlers() -> None:
    """dtmf_input ハンドラが HANDLERS に登録されている。"""
    from millicall.workflows.executor import HANDLERS

    assert "dtmf_input" in HANDLERS


def test_menu_registered_in_handlers() -> None:
    """menu ハンドラが HANDLERS に登録されている。"""
    from millicall.workflows.executor import HANDLERS

    assert "menu" in HANDLERS


# =========================================================================== #
# dtmf_input — 単桁モード
# =========================================================================== #


@pytest.mark.asyncio
async def test_dtmf_input_single_digit_returns_digit_and_stores_var() -> None:
    """単桁入力: 有効桁を返し、変数に格納する。"""
    ctx = make_ctx()
    ctx.dtmf = FakeBoundDtmf("5")
    node = make_dtmf_input_node(max_digits=1, variable="my_digit")

    result = await handle_dtmf_input(node, ctx)

    assert result == "5"
    assert ctx.get_var("my_digit") == "5"


@pytest.mark.asyncio
async def test_dtmf_input_single_digit_all_valid_digits() -> None:
    """"0".."9" の各桁が正しくハンドルとして返される。"""
    for digit in "0123456789":
        ctx = make_ctx()
        ctx.dtmf = FakeBoundDtmf(digit)
        node = make_dtmf_input_node(max_digits=1)
        result = await handle_dtmf_input(node, ctx)
        assert result == digit, f"digit={digit} failed: got {result!r}"


@pytest.mark.asyncio
async def test_dtmf_input_single_digit_star_returns_timeout() -> None:
    """単桁で "*" が入力された場合は "timeout" を返す（有効ハンドルなし）。"""
    ctx = make_ctx()
    ctx.dtmf = FakeBoundDtmf("*")
    node = make_dtmf_input_node(max_digits=1)

    result = await handle_dtmf_input(node, ctx)

    assert result == "timeout"


@pytest.mark.asyncio
async def test_dtmf_input_single_digit_empty_returns_timeout() -> None:
    """単桁でタイムアウト（空文字列）の場合は "timeout" を返す。"""
    ctx = make_ctx()
    ctx.dtmf = FakeBoundDtmf("")
    node = make_dtmf_input_node(max_digits=1)

    result = await handle_dtmf_input(node, ctx)

    assert result == "timeout"


# =========================================================================== #
# dtmf_input — 複数桁モード
# =========================================================================== #


@pytest.mark.asyncio
async def test_dtmf_input_multi_digit_nonempty_returns_done() -> None:
    """複数桁で桁が収集できた場合は "done" を返す。"""
    ctx = make_ctx()
    ctx.dtmf = FakeBoundDtmf("1234")
    node = make_dtmf_input_node(max_digits=4, variable="pin")

    result = await handle_dtmf_input(node, ctx)

    assert result == "done"
    assert ctx.get_var("pin") == "1234"


@pytest.mark.asyncio
async def test_dtmf_input_multi_digit_empty_returns_timeout() -> None:
    """複数桁でタイムアウト（空文字列）の場合は "timeout" を返す。"""
    ctx = make_ctx()
    ctx.dtmf = FakeBoundDtmf("")
    node = make_dtmf_input_node(max_digits=4)

    result = await handle_dtmf_input(node, ctx)

    assert result == "timeout"
    assert ctx.get_var("dtmf_result") == ""


# =========================================================================== #
# dtmf_input — ctx.dtmf が None
# =========================================================================== #


@pytest.mark.asyncio
async def test_dtmf_input_no_dtmf_returns_timeout() -> None:
    """ctx.dtmf が None のとき "timeout" を返し、変数を空で格納する。"""
    ctx = make_ctx()
    # ctx.dtmf = None (デフォルト)
    node = make_dtmf_input_node(max_digits=1, variable="result")

    result = await handle_dtmf_input(node, ctx)

    assert result == "timeout"
    assert ctx.get_var("result") == ""


# =========================================================================== #
# dtmf_input — TTS プロンプト再生
# =========================================================================== #


@pytest.mark.asyncio
async def test_dtmf_input_tts_prompt_calls_say() -> None:
    """prompt_mode=tts のとき ctx.primitives.say() が呼ばれる。"""
    ctx = make_ctx()
    ctx.primitives = make_fake_primitives()
    ctx.dtmf = FakeBoundDtmf("1")
    node = make_dtmf_input_node(
        max_digits=1,
        prompt_mode="tts",
        prompt_text="番号を入力してください",
    )

    await handle_dtmf_input(node, ctx)

    ctx.primitives.say.assert_awaited_once_with("番号を入力してください")


@pytest.mark.asyncio
async def test_dtmf_input_beep_prompt_calls_play_file() -> None:
    """prompt_mode=beep のとき ctx.call_control.play_file() が tone_stream で呼ばれる。"""
    ctx = make_ctx()
    ctx.call_control = make_fake_call_control()
    ctx.dtmf = FakeBoundDtmf("1")
    node = make_dtmf_input_node(max_digits=1, prompt_mode="beep")

    await handle_dtmf_input(node, ctx)

    ctx.call_control.play_file.assert_awaited_once_with("tone_stream://%(200,100,800)")


@pytest.mark.asyncio
async def test_dtmf_input_none_prompt_does_not_call_say() -> None:
    """prompt_mode=none のとき say() は呼ばれない。"""
    ctx = make_ctx()
    ctx.primitives = make_fake_primitives()
    ctx.dtmf = FakeBoundDtmf("1")
    node = make_dtmf_input_node(max_digits=1, prompt_mode="none")

    await handle_dtmf_input(node, ctx)

    ctx.primitives.say.assert_not_called()


@pytest.mark.asyncio
async def test_dtmf_input_template_expanded_in_prompt() -> None:
    """prompt_text の {{var}} が展開されてから say() が呼ばれる。"""
    ctx = make_ctx()
    ctx.set_var("caller", "田中")
    ctx.primitives = make_fake_primitives()
    ctx.dtmf = FakeBoundDtmf("1")
    node = make_dtmf_input_node(
        max_digits=1,
        prompt_mode="tts",
        prompt_text="{{caller}}様、番号を押してください",
    )

    await handle_dtmf_input(node, ctx)

    ctx.primitives.say.assert_awaited_once_with("田中様、番号を押してください")


@pytest.mark.asyncio
async def test_dtmf_input_collect_called_with_correct_params() -> None:
    """collect() が config の max_digits / timeout / terminator で呼ばれる。"""
    ctx = make_ctx()
    ctx.dtmf = FakeBoundDtmf("3")
    node = make_dtmf_input_node(max_digits=2, timeout=8, terminator="*")

    await handle_dtmf_input(node, ctx)

    ctx.dtmf.collect.assert_awaited_once_with(max_digits=2, timeout=8, terminator="*")


# =========================================================================== #
# menu — 有効桁が入力される
# =========================================================================== #


@pytest.mark.asyncio
async def test_menu_valid_digit_returned_immediately() -> None:
    """初回で有効桁が入力されたら即座に返す。"""
    ctx = make_ctx()
    ctx.dtmf = FakeBoundDtmf("3")
    node = make_menu_node()

    result = await handle_menu(node, ctx)

    assert result == "3"


@pytest.mark.asyncio
async def test_menu_valid_digit_zero() -> None:
    """0 も有効桁として返される。"""
    ctx = make_ctx()
    ctx.dtmf = FakeBoundDtmf("0")
    node = make_menu_node()

    result = await handle_menu(node, ctx)

    assert result == "0"


# =========================================================================== #
# menu — リトライ
# =========================================================================== #


@pytest.mark.asyncio
async def test_menu_timeout_then_valid_on_retry() -> None:
    """初回タイムアウト → リトライで有効桁 → その桁を返す。"""
    ctx = make_ctx()
    ctx.primitives = make_fake_primitives()

    # 1回目: タイムアウト(空文字), 2回目: "5"
    fake_dtmf = MagicMock()
    fake_dtmf.collect = AsyncMock(side_effect=["", "5"])
    ctx.dtmf = fake_dtmf

    node = make_menu_node(max_retries=1, invalid_text="もう一度押してください")

    result = await handle_menu(node, ctx)

    assert result == "5"
    # invalid_text の say が 1 回呼ばれたこと
    ctx.primitives.say.assert_awaited_once_with("もう一度押してください")


@pytest.mark.asyncio
async def test_menu_all_timeout_returns_timeout() -> None:
    """max_retries+1 回すべてタイムアウトしたら "timeout" を返す。"""
    ctx = make_ctx()

    fake_dtmf = MagicMock()
    # max_retries=2 → 3回試行
    fake_dtmf.collect = AsyncMock(side_effect=["", "", ""])
    ctx.dtmf = fake_dtmf

    node = make_menu_node(max_retries=2)

    result = await handle_menu(node, ctx)

    assert result == "timeout"
    assert fake_dtmf.collect.await_count == 3


@pytest.mark.asyncio
async def test_menu_invalid_text_played_on_each_retry() -> None:
    """タイムアウトのたびに invalid_text が再生される（最後の試行後は除く）。"""
    ctx = make_ctx()
    ctx.primitives = make_fake_primitives()

    # max_retries=2 → 3回試行、全タイムアウト
    fake_dtmf = MagicMock()
    fake_dtmf.collect = AsyncMock(side_effect=["", "", ""])
    ctx.dtmf = fake_dtmf

    node = make_menu_node(max_retries=2, invalid_text="無効です")

    result = await handle_menu(node, ctx)

    assert result == "timeout"
    # リトライ 2 回分（試行 1 後と試行 2 後）だけ invalid_text が再生される
    # 最後の試行（attempt=2=max_retries）後は再生しない
    assert ctx.primitives.say.await_count == 2


@pytest.mark.asyncio
async def test_menu_no_invalid_text_no_say_on_retry() -> None:
    """invalid_text が空のときはリトライ時に say() を呼ばない。"""
    ctx = make_ctx()
    ctx.primitives = make_fake_primitives()

    fake_dtmf = MagicMock()
    fake_dtmf.collect = AsyncMock(side_effect=["", "7"])
    ctx.dtmf = fake_dtmf

    # prompt_mode=none → say は invalid_text 再生にのみ使われるはず
    node = make_menu_node(max_retries=1, invalid_text="", prompt_mode="none")

    result = await handle_menu(node, ctx)

    assert result == "7"
    ctx.primitives.say.assert_not_called()


# =========================================================================== #
# menu — ctx.dtmf が None
# =========================================================================== #


@pytest.mark.asyncio
async def test_menu_no_dtmf_returns_timeout() -> None:
    """ctx.dtmf が None のとき即座に "timeout" を返す。"""
    ctx = make_ctx()
    # ctx.dtmf = None (デフォルト)
    node = make_menu_node()

    result = await handle_menu(node, ctx)

    assert result == "timeout"


# =========================================================================== #
# menu — TTS プロンプト
# =========================================================================== #


@pytest.mark.asyncio
async def test_menu_tts_prompt_played_on_each_attempt() -> None:
    """prompt_mode=tts のとき、各試行でプロンプトが再生される。"""
    ctx = make_ctx()
    ctx.primitives = make_fake_primitives()

    fake_dtmf = MagicMock()
    # 2回試行: 1回目タイムアウト、2回目 "1"
    fake_dtmf.collect = AsyncMock(side_effect=["", "1"])
    ctx.dtmf = fake_dtmf

    node = make_menu_node(
        max_retries=1,
        prompt_mode="tts",
        prompt_text="番号を押してください",
        invalid_text="",
    )

    result = await handle_menu(node, ctx)

    assert result == "1"
    # 各試行でプロンプトが再生される → 2回
    assert ctx.primitives.say.await_count == 2


@pytest.mark.asyncio
async def test_menu_collect_uses_empty_terminator() -> None:
    """menu の collect は terminator='' で呼ばれる（終端キーなし）。"""
    ctx = make_ctx()
    fake_dtmf = MagicMock()
    fake_dtmf.collect = AsyncMock(return_value="2")
    ctx.dtmf = fake_dtmf

    node = make_menu_node(timeout=7)

    await handle_menu(node, ctx)

    fake_dtmf.collect.assert_awaited_with(max_digits=1, timeout=7, terminator="")
