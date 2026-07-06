import base64
import hashlib

from cryptography.fernet import Fernet


def _derive_key(master_key: str) -> bytes:
    digest = hashlib.sha256(master_key.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


class SecretBox:
    """master_key から決定論的に導出した Fernet で秘密を暗号化する。"""

    def __init__(self, master_key: str) -> None:
        self._fernet = Fernet(_derive_key(master_key))

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str, ttl: int | None = None) -> str:
        # ttl 指定時は Fernet の埋め込みタイムスタンプで有効期限を検証する
        # （期限切れ・改ざんは InvalidToken を送出）。
        return self._fernet.decrypt(token.encode("ascii"), ttl=ttl).decode("utf-8")


def mask_secret(value: str) -> str:
    """APIレスポンス用マスク。末尾4文字のみ残す。"""
    if not value:
        return ""
    if len(value) <= 4:
        return "****"
    return "****" + value[-4:]
