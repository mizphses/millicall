"""秘密値の乱数生成ユーティリティ（デフォルト認証情報の全廃のため常に自動生成）。"""

import secrets
import string

_ALPHABET = string.ascii_letters + string.digits


def _generate_token(length: int) -> str:
    """英数字のみで構成された length 文字のランダムトークンを返す。"""
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


def generate_password(length: int = 24) -> str:
    """汎用パスワードを生成する（英数字、デフォルト 24 文字）。"""
    return _generate_token(length)


def generate_sip_password(length: int = 24) -> str:
    # SIP 認証で問題になりにくい英数字のみ。ユーザー指定は不可。
    return _generate_token(length)
