from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

_hasher = PasswordHasher()
_SALT = "millicall.session.v1"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(hashed: str, password: str) -> bool:
    try:
        return _hasher.verify(hashed, password)
    except VerifyMismatchError:
        return False


def _serializer(secret: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret, salt=_SALT)


@dataclass(frozen=True)
class SessionData:
    """セッショントークンのデコード結果。"""

    uid: int
    epoch: int


def issue_session(secret: str, user_id: int, epoch: int) -> str:
    """セッショントークンを発行する。uidとepochを埋め込む。"""
    return _serializer(secret).dumps({"uid": user_id, "ep": epoch})


def read_session(secret: str, token: str, max_age: int) -> SessionData | None:
    """セッショントークンを検証してSessionDataを返す。

    "ep"キーが存在しないレガシートークンはepoch=0として扱う（後方互換）。
    """
    try:
        data = _serializer(secret).loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None
    uid = data.get("uid")
    if not isinstance(uid, int):
        return None
    epoch = data.get("ep", 0)  # レガシートークン: ep無し → epoch 0
    if not isinstance(epoch, int):
        return None
    return SessionData(uid=uid, epoch=epoch)


def bump_session_epoch(user) -> None:
    """ユーザーのセッションepochをインクリメントして既存Cookie全て無効化する。

    呼び出し後、呼び出し元がDBにflush/commitすること。
    """
    user.session_epoch = (user.session_epoch or 0) + 1
