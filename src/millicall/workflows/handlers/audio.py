"""音声/TTS 系ノードハンドラ — play_audio / transfer / voicemail / human_escalation
(Phase 4b Task 5).

各ハンドラは :func:`~millicall.workflows.executor.register_handler` を使って
グローバルレジストリに登録される。このモジュールをインポートするだけで登録が完了する。

設計原則:
  * **play_audio**: config.file_path が指定されている場合はそのファイルを直接再生
    （ctx.call_control.play_file）。それ以外は ctx.render で {{var}} 展開した
    tts_text を ctx.primitives.say() で合成・再生する。
  * **transfer**: blind のみ（コントローラ裁定 #4）。ctx.call_control.transfer() を呼び、
    terminal を返す（出力ハンドル空なので executor が正常終了とみなす）。
  * **voicemail**: greeting_text を再生（ctx.primitives.say）→ uuid_record で録音
    （ctx.primitives.record）→ 録音パスを ``voicemail_path`` 変数に格納 → terminal。
    メール配送は Phase 後送り（コントローラ裁定 #5）。
  * **human_escalation**: announcement_text を再生 → ctx.call_control.transfer で転送
    → terminal。summary_to_agent は将来の AI 会話セッションとの統合時に実装
    （Task 7 の ConversationSession 参照が必要なため現 Phase はスキップ）。
  * ctx リソース（primitives / call_control）が None の場合は graceful skip
    （unit テストで実 ESL 不要）。

ctx への要求:
  * ``ctx.primitives`` が None でないこと（TTS/録音が必要なノードで使用）。
    - say(text: str) → Awaitable[float]  # CallPrimitives.say
    - record(path: str, max_seconds: int) → Awaitable[str]  # CallPrimitives.record (Task 5 追加)
  * ``ctx.call_control`` — play_file / transfer が必要なノードで使用。
  * ``ctx.tts_dir`` — voicemail の録音先ディレクトリ計算に使用（None の場合は tempdir）。
  * ``ctx.uuid`` — 録音ファイル名の一意化に使用。
"""

from __future__ import annotations

import logging
import re
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

from millicall.workflows.executor import register_handler

_logger = logging.getLogger(__name__)

# mailbox はワークフロー作者が任意に設定できるため、録音パスに補間する前に
# 安全な slug へ正規化する（英数・アンダースコア・ハイフン以外を "_" に置換）。
# これにより ESL メタ文字・パス区切りの混入を防ぐ（record() 側でも二重に検証）。
_MAILBOX_SANITIZE_RE = re.compile(r"[^A-Za-z0-9_-]")

# play_audio の file_path は ESL コマンド (uuid_broadcast <uuid> <path> aleg) に
# そのまま補間されるため、他の ESL シンク（record/transfer/send_dtmf）と同じく
# 厳格 allowlist で検証する。空白/改行/ESL 区切り(&;|`)を排し、英数・ドット・
# アンダースコア・ハイフン・スラッシュ・コロン（tone_stream:// 等）のみ許可。
_VALID_AUDIO_PATH_RE = re.compile(r"^[A-Za-z0-9._:/-]{1,512}$")


def _safe_mailbox(mailbox: str) -> str:
    slug = _MAILBOX_SANITIZE_RE.sub("_", mailbox or "")[:64]
    return slug or "default"

if TYPE_CHECKING:
    from millicall.workflows.context import ChannelContext

# voicemail の録音パスを格納する変数名
_VOICEMAIL_PATH_VAR = "voicemail_path"


def _voicemail_dir(ctx: ChannelContext) -> Path:
    """voicemail 録音ディレクトリのパスを返す。

    ctx.tts_dir が設定されている場合はその隣（parent/voicemail）を使う。
    設定されていない場合は OS の一時ディレクトリ以下を使う。
    """
    if ctx.tts_dir is not None:
        return Path(ctx.tts_dir).parent / "voicemail"
    return Path(tempfile.gettempdir()) / "millicall_voicemail"


# --------------------------------------------------------------------------- #
# play_audio ハンドラ
# --------------------------------------------------------------------------- #


@register_handler("play_audio")
async def handle_play_audio(node: object, ctx: ChannelContext) -> None:
    """音声再生ノード。

    config.file_path が空でない場合はそのファイルを直接再生する（TTS 不使用）。
    空の場合は config.tts_text を ``{{var}}`` 展開したうえで TTS 合成・再生する。
    戻り値は None（単一出力ノード → "out" 既定遷移）。
    """
    config = node.config  # type: ignore[attr-defined]

    if config.file_path:
        # ファイル直接再生（TTS 不使用）。ESL インジェクション防止のため allowlist 検証し、
        # 不正なら再生をスキップする（play_audio は "out" のみなので raise せず継続）。
        if not _VALID_AUDIO_PATH_RE.match(config.file_path):
            _logger.warning(
                "play_audio: file_path を allowlist で拒否し再生をスキップ: %r",
                config.file_path,
            )
        elif ctx.call_control is not None:
            await ctx.call_control.play_file(config.file_path)
    else:
        # TTS 合成→再生
        text = ctx.render(config.tts_text)
        if text and ctx.primitives is not None:
            await ctx.primitives.say(text)

    return None


# --------------------------------------------------------------------------- #
# transfer ハンドラ
# --------------------------------------------------------------------------- #


@register_handler("transfer")
async def handle_transfer(node: object, ctx: ChannelContext) -> None:
    """転送ノード（blind のみ、コントローラ裁定 #4）。

    ctx.call_control.transfer() で blind 転送を発行し、terminal を返す。
    transfer ノードは出力ハンドルが空（[]）のため executor が正常終了とみなす。
    """
    config = node.config  # type: ignore[attr-defined]

    if ctx.call_control is not None:
        await ctx.call_control.transfer(config.destination)

    return None


# --------------------------------------------------------------------------- #
# voicemail ハンドラ
# --------------------------------------------------------------------------- #


@register_handler("voicemail")
async def handle_voicemail(node: object, ctx: ChannelContext) -> None:
    """ボイスメールノード（コントローラ裁定 #5: 録音+パス記録まで）。

    1. config.greeting_text が空でなければ TTS 再生（{{var}} 展開後）。
    2. uuid_record で録音（ctx.primitives.record）。
    3. 録音パスを ``voicemail_path`` 変数に格納。
    4. terminal（出力ハンドル空）。

    ctx.primitives が None の場合は録音をスキップし、voicemail_path を空文字で格納する。
    メール配送・人間向け一覧表示は Phase 後続で実装（裁定 #5）。
    """
    config = node.config  # type: ignore[attr-defined]

    # 1. Greeting 再生
    if config.greeting_text and ctx.primitives is not None:
        greeting = ctx.render(config.greeting_text)
        if greeting:
            await ctx.primitives.say(greeting)

    # 2. 録音 & 3. パス格納
    if ctx.primitives is not None:
        vm_dir = _voicemail_dir(ctx)
        vm_dir.mkdir(parents=True, exist_ok=True)
        ts = int(time.time())
        path_str = str(vm_dir / f"vm_{ctx.uuid}_{_safe_mailbox(config.mailbox)}_{ts}.wav")
        await ctx.primitives.record(path_str, config.max_seconds)
        ctx.set_var(_VOICEMAIL_PATH_VAR, path_str)
    else:
        # primitives なし → 録音スキップ
        ctx.set_var(_VOICEMAIL_PATH_VAR, "")

    return None


# --------------------------------------------------------------------------- #
# human_escalation ハンドラ
# --------------------------------------------------------------------------- #


@register_handler("human_escalation")
async def handle_human_escalation(node: object, ctx: ChannelContext) -> None:
    """有人エスカレーションノード。

    1. config.announcement_text が空でなければ TTS 再生（{{var}} 展開後）。
    2. ctx.call_control.transfer で blind 転送。
    3. terminal（出力ハンドル空）。

    config.summary_to_agent（直近の ConversationSession 要約を転送先エージェントへ渡す）は
    Task 7（AI 会話セッション統合）実装後に有効化する。現 Phase では設定値を読むが
    実際の要約生成・連携はスタブ（未実装）。
    """
    config = node.config  # type: ignore[attr-defined]

    # 1. Announcement 再生
    if config.announcement_text and ctx.primitives is not None:
        announcement = ctx.render(config.announcement_text)
        if announcement:
            await ctx.primitives.say(announcement)

    # 2. 転送
    if ctx.call_control is not None:
        await ctx.call_control.transfer(config.destination)

    return None
