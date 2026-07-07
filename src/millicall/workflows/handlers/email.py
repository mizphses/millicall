"""メール通知ノードハンドラ — email_notify (Phase 4b Task 8).

このモジュールをインポートするだけで ``email_notify`` ハンドラが
グローバルレジストリに登録される。

出力ハンドル:
  * ``"success"`` — メール送信が正常に完了した場合。
  * ``"error"``   — 以下のいずれかの場合（フロー継続、例外伝播なし）:
      - ``ctx.smtp`` が None（未設定）
      - 宛先・件名にヘッダインジェクションが検出された（ValueError）
      - SMTP 接続/認証/送信の失敗（aiosmtplib 例外）
      - その他すべての例外

ヘッダインジェクション対策は ``SmtpEmailSender.send()`` 側で実施しており、
CR/LF を含む to/subject は ``ValueError`` として早期排除される。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from millicall.workflows.executor import register_handler

if TYPE_CHECKING:
    from millicall.workflows.context import ChannelContext


@register_handler("email_notify")
async def handle_email_notify(node: object, ctx: ChannelContext) -> str:
    """メール通知ノードハンドラ。

    ``ctx.smtp`` (SmtpEmailSender) を使って to/subject/body を送信する。
    to・subject_template・body_template はいずれも ``ctx.render`` で
    ``{{var}}`` テンプレート展開してから送信する。

    ``ctx.smtp`` が None の場合は即座に ``"error"`` を返す（ランナーファクトリが
    SMTP 設定なしで起動した場合の graceful 処理）。

    あらゆる例外は捕捉して ``"error"`` を返し、フロー継続を保証する。
    ヘッダインジェクション（CR/LF 含む to/subject）は SmtpEmailSender 側で
    ValueError として検出され、ここで "error" に変換される。

    Returns:
        "success": 送信成功。
        "error": ctx.smtp 未設定、バリデーションエラー、SMTP エラー等。
    """
    config = node.config  # type: ignore[attr-defined]
    try:
        if ctx.smtp is None:
            return "error"
        to = ctx.render(config.to)
        subject = ctx.render(config.subject_template)
        body = ctx.render(config.body_template)
        await ctx.smtp.send(to=to, subject=subject, body=body)
        return "success"
    except Exception:  # noqa: BLE001
        return "error"
