"""Task 8: SmtpEmailSender のユニットテスト.

テスト方針:
  - aiosmtplib.send をモックして実際のネットワーク接続を行わない。
  - ヘッダインジェクション、不正アドレス、smtp_host 未設定の場合に ValueError が
    送出されることを確認する。
  - 正常送信パスで EmailMessage の各フィールドおよび aiosmtplib.send の
    呼び出し引数が正しいことを確認する。
"""

from __future__ import annotations

from email.message import EmailMessage
from unittest.mock import AsyncMock, patch

import pytest

from millicall.workflows.email_sender import SmtpEmailSender

# --------------------------------------------------------------------------- #
# ヘルパ
# --------------------------------------------------------------------------- #


def make_sender(
    *,
    host: str = "smtp.example.com",
    port: int = 587,
    username: str = "user@example.com",
    password: str = "secret",
    from_addr: str = "",
    use_starttls: bool = True,
    timeout: int = 15,
) -> SmtpEmailSender:
    return SmtpEmailSender(
        host=host,
        port=port,
        username=username,
        password=password,
        from_addr=from_addr,
        use_starttls=use_starttls,
        timeout=timeout,
    )


# --------------------------------------------------------------------------- #
# smtp_host が空の場合
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_send_raises_when_host_empty() -> None:
    """smtp_host が空の場合は「メール送信が無効」ValueError が送出される。"""
    sender = make_sender(host="")
    with pytest.raises(ValueError, match="MILLICALL_SMTP_HOST"):
        await sender.send(to="to@example.com", subject="件名", body="本文")


# --------------------------------------------------------------------------- #
# ヘッダインジェクション対策
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "to, subject",
    [
        ("bad\r@example.com", "件名"),
        ("bad\n@example.com", "件名"),
        ("to@example.com", "件名\r注入"),
        ("to@example.com", "件名\n注入"),
    ],
)
async def test_header_injection_in_to_or_subject_raises(to: str, subject: str) -> None:
    """to または subject に CR/LF が含まれる場合は ValueError。"""
    sender = make_sender()
    with pytest.raises(ValueError, match="CR/LF|ヘッダインジェクション"):
        await sender.send(to=to, subject=subject, body="本文")


# --------------------------------------------------------------------------- #
# 不正な宛先アドレス
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_to",
    [
        "notanemail",        # "@" なし
        "has space@ex.com",  # スペースあり
        "no at sign here",   # スペースあり + "@" なし
    ],
)
async def test_invalid_to_address_raises(invalid_to: str) -> None:
    """不正な宛先アドレスは ValueError を送出する。"""
    sender = make_sender()
    with pytest.raises(ValueError, match="宛先アドレス|@"):
        await sender.send(to=invalid_to, subject="件名", body="本文")


# --------------------------------------------------------------------------- #
# 正常送信パス
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_happy_path_calls_aiosmtplib_send() -> None:
    """正常パスで aiosmtplib.send が正しい引数で呼ばれることを確認する。"""
    sender = make_sender(
        host="smtp.example.com",
        port=465,
        username="user@example.com",
        password="pass",
        from_addr="noreply@example.com",
        use_starttls=False,
        timeout=10,
    )

    with patch("millicall.workflows.email_sender.aiosmtplib.send", new_callable=AsyncMock) as mock_send:
        await sender.send(to="dest@example.com", subject="テスト件名", body="テスト本文")

    mock_send.assert_awaited_once()
    call_kwargs = mock_send.call_args

    # 第一引数は EmailMessage であることを確認
    msg: EmailMessage = call_kwargs.args[0]
    assert isinstance(msg, EmailMessage)
    assert msg["From"] == "noreply@example.com"
    assert msg["To"] == "dest@example.com"
    assert msg["Subject"] == "テスト件名"
    assert "テスト本文" in msg.get_body().get_content()  # type: ignore[union-attr]

    # キーワード引数を確認
    kwargs = call_kwargs.kwargs
    assert kwargs["hostname"] == "smtp.example.com"
    assert kwargs["port"] == 465
    assert kwargs["start_tls"] is False
    assert kwargs["timeout"] == 10


@pytest.mark.asyncio
async def test_from_addr_falls_back_to_username() -> None:
    """from_addr が空の場合は username を From アドレスとして使用する。"""
    sender = make_sender(
        username="fallback@example.com",
        from_addr="",  # 空 → username へフォールバック
    )

    with patch("millicall.workflows.email_sender.aiosmtplib.send", new_callable=AsyncMock) as mock_send:
        await sender.send(to="to@example.com", subject="件名", body="本文")

    msg: EmailMessage = mock_send.call_args.args[0]
    assert msg["From"] == "fallback@example.com"


@pytest.mark.asyncio
async def test_starttls_and_auth_forwarded() -> None:
    """STARTTLS フラグと認証情報が aiosmtplib.send に正しく渡される。"""
    sender = make_sender(
        use_starttls=True,
        username="u@example.com",
        password="pw",
    )

    with patch("millicall.workflows.email_sender.aiosmtplib.send", new_callable=AsyncMock) as mock_send:
        await sender.send(to="to@example.com", subject="件名", body="本文")

    kwargs = mock_send.call_args.kwargs
    assert kwargs["start_tls"] is True
    assert kwargs["username"] == "u@example.com"
    assert kwargs["password"] == "pw"


# --------------------------------------------------------------------------- #
# from_settings ファクトリ
# --------------------------------------------------------------------------- #


def test_from_settings_factory() -> None:
    """from_settings クラスメソッドが Settings から正しく SmtpEmailSender を生成する。"""
    from millicall.config import Settings

    settings = Settings(
        smtp_host="mail.example.com",
        smtp_port=25,
        smtp_username="u@example.com",
        smtp_password="pw",
        smtp_from="from@example.com",
        smtp_starttls=False,
        smtp_timeout=30,
    )
    sender = SmtpEmailSender.from_settings(settings)

    assert sender._host == "mail.example.com"
    assert sender._port == 25
    assert sender._username == "u@example.com"
    assert sender._password == "pw"
    assert sender._from_addr == "from@example.com"
    assert sender._use_starttls is False
    assert sender._timeout == 30
