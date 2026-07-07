"""非同期 SMTP メール送信ユーティリティ (Phase 4b Task 8).

``SmtpEmailSender`` は aiosmtplib を使ってプレーンテキストメールを送信する。
ヘッダインジェクション対策として、宛先・件名に CR/LF が含まれている場合や
宛先が不正な形式（"@" なし・スペース含む）の場合は ``ValueError`` を送出する。
メッセージは ``email.message.EmailMessage`` で組み立てるため、
ヘッダの文字列連結による注入が構造的に発生しない。
"""

from __future__ import annotations

from email.message import EmailMessage
from typing import TYPE_CHECKING

import aiosmtplib

if TYPE_CHECKING:
    from millicall.config import Settings


def _reject_header_injection(value: str, field: str) -> None:
    """CR または LF を含む場合に ValueError を送出する（ヘッダインジェクション対策）。"""
    if "\r" in value or "\n" in value:
        raise ValueError(
            f"メールヘッダインジェクションを検出しました: {field} に CR/LF が含まれています"
        )


def _validate_to_address(to: str) -> None:
    """宛先アドレスが最小限の形式要件を満たしているか検証する。

    チェック内容:
      * "@" が含まれること（メールアドレスの必須要件）。
      * スペースが含まれないこと（不正アドレスの簡易判定）。
    これらに違反した場合は ``ValueError`` を送出する。
    """
    if "@" not in to:
        raise ValueError(f"宛先アドレスに '@' が含まれていません: {to!r}")
    if " " in to:
        raise ValueError(f"宛先アドレスにスペースが含まれています: {to!r}")


class SmtpEmailSender:
    """aiosmtplib を使ったプレーンテキスト SMTP 送信クラス。

    ヘッダは ``EmailMessage`` 経由でセットするため、文字列連結によるインジェクションは
    構造上発生しない。加えて、to/subject に CR/LF が含まれる場合は send() の冒頭で
    ``ValueError`` を送出する二重防護を備える。
    """

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        from_addr: str,
        use_starttls: bool,
        timeout: int,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._from_addr = from_addr or username
        self._use_starttls = use_starttls
        self._timeout = timeout

    @classmethod
    def from_settings(cls, settings: Settings) -> SmtpEmailSender:
        """Settings インスタンスから SmtpEmailSender を構築するファクトリメソッド。

        Task 9 のランナーファクトリから呼び出される。
        ``settings.smtp_host`` が空文字の場合でもオブジェクト自体は生成できるが、
        ``send()`` を呼ぶと「メール送信が無効です」エラーが発生する。
        """
        return cls(
            host=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username,
            password=settings.smtp_password,
            from_addr=settings.smtp_from,
            use_starttls=settings.smtp_starttls,
            timeout=settings.smtp_timeout,
        )

    async def send(self, *, to: str, subject: str, body: str) -> None:
        """プレーンテキストメールを送信する。

        送信前に以下の検証を行う:
          1. ``smtp_host`` が設定されているか（空なら ``ValueError``）。
          2. ``to`` / ``subject`` に CR/LF が含まれないか（ヘッダインジェクション対策）。
          3. ``to`` が最小限のメールアドレス形式か（"@" 必須・スペース禁止）。

        検証を通過した場合、``email.message.EmailMessage`` でメッセージを組み立て、
        aiosmtplib で SMTP 送信する。STARTTLS / 認証は設定値に従う。

        Args:
            to: 宛先メールアドレス。
            subject: 件名。
            body: 本文（プレーンテキスト）。

        Raises:
            ValueError: 設定不正・ヘッダインジェクション・不正アドレスの場合。
            aiosmtplib.SMTPException: SMTP レベルのエラー。
        """
        if not self._host:
            raise ValueError(
                "メール送信が無効です: MILLICALL_SMTP_HOST が設定されていません"
            )

        # ヘッダインジェクション対策
        _reject_header_injection(to, "to")
        _reject_header_injection(subject, "subject")

        # 宛先アドレス形式チェック
        _validate_to_address(to)

        # EmailMessage でメッセージを組み立てる（文字列連結を避ける）
        msg = EmailMessage()
        msg["From"] = self._from_addr
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)

        await aiosmtplib.send(
            msg,
            hostname=self._host,
            port=self._port,
            username=self._username or None,
            password=self._password or None,
            start_tls=self._use_starttls,
            timeout=self._timeout,
        )
