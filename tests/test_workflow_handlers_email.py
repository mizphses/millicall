"""Task 8: email_notify ノードハンドラのユニットテスト.

テスト方針:
  - ctx.smtp を AsyncMock の fake sender で差し替えてネットワーク不使用。
  - {{var}} テンプレート展開が to/subject/body に正しく適用されることを確認。
  - ctx.smtp が None の場合・smtp.send が例外を送出した場合に "error" を返すことを確認。
  - executor.HANDLERS に "email_notify" が登録されていることを確認。
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from millicall.workflows.context import ChannelContext
from millicall.workflows.executor import HANDLERS

# ハンドラモジュールのインポート（副作用として登録される）
from millicall.workflows.handlers.email import handle_email_notify

# --------------------------------------------------------------------------- #
# ヘルパ
# --------------------------------------------------------------------------- #


def make_email_notify_node(
    to: str = "to@example.com",
    subject_template: str = "件名",
    body_template: str = "本文",
):
    from millicall.workflows.nodes import EmailNotifyConfig, EmailNotifyNode

    return EmailNotifyNode(
        id="email1",
        type="email_notify",
        config=EmailNotifyConfig(
            to=to,
            subject_template=subject_template,
            body_template=body_template,
        ),
    )


def make_ctx(variables: dict | None = None, smtp=None) -> ChannelContext:
    ctx = ChannelContext(uuid="test-uuid")
    ctx.smtp = smtp
    if variables:
        for k, v in variables.items():
            ctx.set_var(k, v)
    return ctx


# --------------------------------------------------------------------------- #
# ハンドラ登録確認
# --------------------------------------------------------------------------- #


def test_handler_registered_in_executor() -> None:
    """email_notify ハンドラが executor.HANDLERS に登録されていること。"""
    assert "email_notify" in HANDLERS


# --------------------------------------------------------------------------- #
# ctx.smtp が None
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_returns_error_when_smtp_is_none() -> None:
    """ctx.smtp が None の場合は "error" を返す。"""
    node = make_email_notify_node()
    ctx = make_ctx(smtp=None)
    result = await handle_email_notify(node, ctx)
    assert result == "error"


# --------------------------------------------------------------------------- #
# 正常送信 + テンプレート展開
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_happy_path_with_template_expansion() -> None:
    """{{var}} 展開が to/subject/body に適用され、ctx.smtp.send が正しく呼ばれ "success" を返す。"""
    fake_smtp = AsyncMock()
    fake_smtp.send = AsyncMock(return_value=None)

    node = make_email_notify_node(
        to="{{caller_email}}",
        subject_template="着信通知: {{caller_name}}",
        body_template="{{caller_name}} ({{caller_email}}) から着信がありました。",
    )
    ctx = make_ctx(
        variables={
            "caller_email": "alice@example.com",
            "caller_name": "Alice",
        },
        smtp=fake_smtp,
    )

    result = await handle_email_notify(node, ctx)

    assert result == "success"
    fake_smtp.send.assert_awaited_once_with(
        to="alice@example.com",
        subject="着信通知: Alice",
        body="Alice (alice@example.com) から着信がありました。",
    )


# --------------------------------------------------------------------------- #
# ctx.smtp.send が例外を送出
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_returns_error_when_send_raises() -> None:
    """ctx.smtp.send が例外を送出した場合は "error" を返す（フロー継続）。"""
    fake_smtp = AsyncMock()
    fake_smtp.send = AsyncMock(side_effect=RuntimeError("SMTP 接続失敗"))

    node = make_email_notify_node()
    ctx = make_ctx(smtp=fake_smtp)

    result = await handle_email_notify(node, ctx)
    assert result == "error"


@pytest.mark.asyncio
async def test_returns_error_when_send_raises_value_error() -> None:
    """ヘッダインジェクション等の ValueError も "error" として処理される。"""
    fake_smtp = AsyncMock()
    fake_smtp.send = AsyncMock(side_effect=ValueError("ヘッダインジェクション"))

    node = make_email_notify_node()
    ctx = make_ctx(smtp=fake_smtp)

    result = await handle_email_notify(node, ctx)
    assert result == "error"


# --------------------------------------------------------------------------- #
# 変数が未定義の場合（空文字に展開される）
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_undefined_variable_expands_to_empty() -> None:
    """未定義変数は空文字に展開され、送信自体は試みられる。"""
    fake_smtp = AsyncMock()
    fake_smtp.send = AsyncMock(return_value=None)

    node = make_email_notify_node(
        to="fixed@example.com",
        subject_template="通知: {{undefined_var}}",
        body_template="値: {{also_undefined}}",
    )
    ctx = make_ctx(smtp=fake_smtp)

    result = await handle_email_notify(node, ctx)

    assert result == "success"
    fake_smtp.send.assert_awaited_once_with(
        to="fixed@example.com",
        subject="通知: ",
        body="値: ",
    )
