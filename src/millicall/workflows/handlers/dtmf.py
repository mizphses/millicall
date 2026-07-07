"""DTMF 収集系ノードハンドラ — dtmf_input / menu (Phase 4b Task 6).

各ハンドラは :func:`~millicall.workflows.executor.register_handler` を使って
グローバルレジストリに登録される。このモジュールをインポートするだけで登録が完了する。

設計原則:
  * **dtmf_input**: プロンプト再生 → ctx.dtmf.collect() で桁収集 → 変数格納 →
    出力ハンドル決定。max_digits==1 のときは収集した桁が直接ハンドル名になる
    （"0".."9" のいずれか、それ以外は "timeout"）。max_digits>1 は "done"/"timeout"。
  * **menu**: max_retries+1 回ループしながら単一桁を収集する IVR メニュー。
    有効桁（"0".."9"）が入力されればそのまま返す。タイムアウト/無効の場合は
    invalid_text を再生してリトライ。すべての試行を使い切ったら "timeout" を返す。
  * ctx.dtmf が None のとき（DTMF コレクタ未接続）は即座に "timeout" を返す
    （unit テストで ESL 不要の graceful fallback）。
  * ctx.primitives / ctx.call_control が None のときは対応する副作用をスキップ
    （unit テストで実 ESL 不要）。

ctx への要求:
  * ``ctx.dtmf`` — BoundDtmf: collect(max_digits, timeout, terminator) -> str
    （Task 9 のランナーファクトリが DtmfCollector.bind(uuid) をセットする）。
  * ``ctx.primitives.say(text)`` — TTS 再生（prompt_mode == "tts" のとき使用）。
  * ``ctx.call_control.play_file(path)`` — ファイル/tone_stream 再生
    （prompt_mode == "beep" のとき使用）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from millicall.workflows.executor import register_handler

if TYPE_CHECKING:
    from millicall.workflows.context import ChannelContext

# FreeSWITCH DTMF 有効桁セット（menu / dtmf_input の単桁ハンドル）
_VALID_DIGITS: frozenset[str] = frozenset("0123456789")

# beep 再生に使う FreeSWITCH tone_stream（200ms音 + 100ms無音、800Hz）
_BEEP_TONE = "tone_stream://%(200,100,800)"


# --------------------------------------------------------------------------- #
# 共有プロンプトヘルパ
# --------------------------------------------------------------------------- #


async def _play_prompt(
    prompt_mode: str,
    prompt_text: str,
    ctx: ChannelContext,
) -> None:
    """プロンプトモードに従ってプロンプトを再生する。

    * ``tts``: prompt_text が空でなく ctx.primitives が利用可能なら
      ``ctx.primitives.say(ctx.render(prompt_text))`` を呼ぶ。
    * ``beep``: ctx.call_control が利用可能なら tone_stream を再生する。
    * ``none``: 何もしない。
    """
    if prompt_mode == "tts":
        text = ctx.render(prompt_text) if prompt_text else ""
        if text and ctx.primitives is not None:
            await ctx.primitives.say(text)
    elif prompt_mode == "beep":
        if ctx.call_control is not None:
            await ctx.call_control.play_file(_BEEP_TONE)
    # "none" は何もしない


# --------------------------------------------------------------------------- #
# dtmf_input ハンドラ
# --------------------------------------------------------------------------- #


@register_handler("dtmf_input")
async def handle_dtmf_input(node: object, ctx: ChannelContext) -> str:
    """DTMF 入力収集ノード。

    1. config.prompt_mode に従いプロンプトを再生する。
    2. ctx.dtmf が None なら "timeout" を返す（DTMF コレクタ未接続）。
    3. ctx.dtmf.collect() で最大 config.max_digits 桁を収集する。
    4. 収集結果を config.variable に格納する。
    5. 出力ハンドルを決定して返す:
       * max_digits == 1: 収集桁が "0".."9" ならその桁、それ以外は "timeout"。
       * max_digits > 1: 収集桁が空でなければ "done"、空なら "timeout"。
    """
    config = node.config  # type: ignore[attr-defined]

    # 1. プロンプト再生
    await _play_prompt(config.prompt_mode, config.prompt_text, ctx)

    # 2. DTMF コレクタ未接続 → タイムアウト扱い
    if ctx.dtmf is None:
        ctx.set_var(config.variable, "")
        return "timeout"

    # 3. 桁収集
    digits: str = await ctx.dtmf.collect(
        max_digits=config.max_digits,
        timeout=config.timeout,
        terminator=config.terminator,
    )

    # 4. 変数格納
    ctx.set_var(config.variable, digits)

    # 5. ハンドル決定
    if config.max_digits == 1:
        # 単桁: "0".."9" のみ有効ハンドル（"*"/"#" や空はタイムアウト扱い）
        if digits in _VALID_DIGITS:
            return digits
        return "timeout"
    else:
        # 複数桁: 何か入力があれば "done"、タイムアウトで空なら "timeout"
        return "done" if digits else "timeout"


# --------------------------------------------------------------------------- #
# menu ハンドラ
# --------------------------------------------------------------------------- #


@register_handler("menu")
async def handle_menu(node: object, ctx: ChannelContext) -> str:
    """IVR メニューノード。

    max_retries + 1 回のループで単一桁の入力を収集する。
    有効桁（"0".."9"）が入力されれば即座に返す（executor がエッジを解決する）。
    タイムアウト/無効の場合は invalid_text を再生してリトライし、
    すべての試行を消費したら "timeout" を返す。

    ctx.dtmf が None の場合は即座に "timeout" を返す。
    """
    config = node.config  # type: ignore[attr-defined]

    # DTMF コレクタ未接続 → 即タイムアウト
    if ctx.dtmf is None:
        return "timeout"

    for attempt in range(config.max_retries + 1):
        # プロンプト再生（リトライ時も毎回再生）
        await _play_prompt(config.prompt_mode, config.prompt_text, ctx)

        # 単桁収集（終端キーなし: terminator=""）
        digit: str = await ctx.dtmf.collect(
            max_digits=1,
            timeout=config.timeout,
            terminator="",
        )

        if digit in _VALID_DIGITS:
            # 有効桁 → 呼び出し元エグゼキュータがエッジを解決する
            return digit

        # 無効 / タイムアウト: 最後の試行でなければ invalid_text を再生してリトライ
        is_last_attempt = attempt >= config.max_retries
        if not is_last_attempt and config.invalid_text and ctx.primitives is not None:
            await ctx.primitives.say(ctx.render(config.invalid_text))

    return "timeout"
