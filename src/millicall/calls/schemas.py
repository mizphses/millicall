from pydantic import BaseModel, ConfigDict, Field


class CallCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    from_extension: str = Field(..., pattern=r"^[0-9]{2,6}$")
    to: str = Field(..., pattern=r"^[0-9*#]{2,30}$")


class CallCreated(BaseModel):
    call_uuid: str
