from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# 発信権限の許可値（トールフラウド対策 §7）
CallingPermission = Literal["internal", "domestic", "international"]


class ExtensionCreate(BaseModel):
    # sip_password は受け付けない（extra フィールドは無視）。
    model_config = ConfigDict(extra="ignore")

    number: str = Field(..., pattern=r"^[0-9]{2,6}$")
    display_name: str = Field(..., min_length=1, max_length=100)
    # 省略時は "domestic"（国際発信デフォルト禁止の原則に従う）
    calling_permission: CallingPermission = "domestic"


class ExtensionUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    enabled: bool | None = None
    calling_permission: CallingPermission | None = None


class ExtensionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    number: str
    display_name: str
    sip_password: str
    enabled: bool
    calling_permission: str
