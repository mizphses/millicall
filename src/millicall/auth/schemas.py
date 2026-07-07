from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator


class LoginRequest(BaseModel):
    username: str
    password: str


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    display_name: str
    role: str
    email: str | None = None
    enabled: bool = True
    origin: str = "local"
    totp_enabled: bool = False

    @model_validator(mode="before")
    @classmethod
    def _compute_totp_enabled(cls, data: Any) -> Any:
        # ORM オブジェクトの場合は totp_secret の有無から totp_enabled を算出。
        # totp_secret / session_epoch / hashed_password は決して露出しない。
        if hasattr(data, "totp_secret"):
            totp_secret = getattr(data, "totp_secret", None)
            return {
                "id": data.id,
                "username": data.username,
                "display_name": data.display_name,
                "role": data.role,
                "email": getattr(data, "email", None),
                "enabled": getattr(data, "enabled", True),
                "origin": getattr(data, "origin", "local"),
                "totp_enabled": totp_secret is not None,
            }
        if isinstance(data, dict) and "totp_secret" in data:
            data = data.copy()
            data["totp_enabled"] = data.pop("totp_secret") is not None
        return data
