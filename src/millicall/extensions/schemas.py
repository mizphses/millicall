from pydantic import BaseModel, ConfigDict, Field


class ExtensionCreate(BaseModel):
    # sip_password は受け付けない（extra フィールドは無視）。
    model_config = ConfigDict(extra="ignore")

    number: str = Field(..., pattern=r"^\d{2,6}$")
    display_name: str = Field(..., min_length=1, max_length=100)


class ExtensionUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    enabled: bool | None = None


class ExtensionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    number: str
    display_name: str
    sip_password: str
    enabled: bool
