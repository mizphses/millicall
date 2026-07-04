from pydantic import BaseModel, ConfigDict, Field

from millicall.routes_config.enums import RouteTargetType


class RouteCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    match_number: str = Field(..., pattern=r"^[0-9*#]{1,30}$")
    target_type: RouteTargetType
    target_value: str = Field(..., min_length=1, max_length=64)
    enabled: bool = True


class RouteUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    target_type: RouteTargetType | None = None
    target_value: str | None = Field(default=None, min_length=1, max_length=64)
    enabled: bool | None = None


class RouteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    match_number: str
    target_type: str
    target_value: str
    enabled: bool
