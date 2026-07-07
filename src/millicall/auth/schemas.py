from pydantic import BaseModel, ConfigDict


class LoginRequest(BaseModel):
    username: str
    password: str


class UserRead(BaseModel):
    """ユーザー情報のレスポンススキーマ。

    機密フィールド（totp_secret / recovery_codes / hashed_password / session_epoch）は
    決してここに含めない。
    totp_enabled は migration 0015 以降 DB カラムから直接読む。
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    display_name: str
    role: str
    email: str | None = None
    enabled: bool = True
    origin: str = "local"
    totp_enabled: bool = False
