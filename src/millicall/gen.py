"""秘密値の乱数生成ユーティリティ（デフォルト認証情報の全廃のため常に自動生成）。"""

import secrets
import string

_ALPHABET = string.ascii_letters + string.digits


def generate_password(length: int = 24) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


def generate_sip_password(length: int = 24) -> str:
    # SIP 認証で問題になりにくい英数字のみ。ユーザー指定は不可。
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))
