from pydantic import BaseModel, ConfigDict, Field

from millicall.models import Trunk


class TrunkCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(..., pattern=r"^[A-Za-z0-9_-]{1,50}$")
    display_name: str = Field(..., min_length=1, max_length=100)
    host: str = Field(..., min_length=1, max_length=100)
    username: str = Field(..., min_length=1, max_length=50)
    password: str = Field(..., min_length=1, max_length=128)
    did_number: str = Field(default="", max_length=30)
    caller_id: str = Field(default="", max_length=30)
    # 着信転送先の内線番号（統一番号プラン）。空 = 着信を受けない。
    inbound_extension: str = Field(default="", pattern=r"^(\d{2,6})?$")
    enabled: bool = True


class TrunkUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    host: str | None = Field(default=None, min_length=1, max_length=100)
    username: str | None = Field(default=None, min_length=1, max_length=50)
    password: str | None = Field(default=None, min_length=1, max_length=128)
    did_number: str | None = Field(default=None, max_length=30)
    caller_id: str | None = Field(default=None, max_length=30)
    # None = 変更しない / "" = 着信を受けない / "NNN" = その内線へ転送
    inbound_extension: str | None = Field(default=None, pattern=r"^(\d{2,6})?$")
    enabled: bool | None = None


class TrunkRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    display_name: str
    host: str
    username: str
    did_number: str
    caller_id: str
    inbound_extension: str
    enabled: bool
    has_password: bool

    @classmethod
    def from_orm_trunk(cls, t: Trunk) -> "TrunkRead":
        return cls(
            id=t.id,
            name=t.name,
            display_name=t.display_name,
            host=t.host,
            username=t.username,
            did_number=t.did_number,
            caller_id=t.caller_id,
            inbound_extension=t.inbound_extension,
            enabled=t.enabled,
            has_password=bool(t.password),
        )
