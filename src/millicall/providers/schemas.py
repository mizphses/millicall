from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from millicall.providers.enums import ProviderKind, ProviderType


class ProviderCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(..., min_length=1, max_length=100)
    type: ProviderType
    kind: ProviderKind
    config: dict[str, Any] = Field(default_factory=dict)
    api_key: str | None = Field(default=None, max_length=8192)
    enabled: bool = True


class ProviderUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str | None = Field(default=None, min_length=1, max_length=100)
    config: dict[str, Any] | None = None
    api_key: str | None = Field(default=None, max_length=8192)
    enabled: bool | None = None


class ProviderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    type: str
    kind: str
    config: dict[str, Any]
    api_key_masked: str | None
    enabled: bool


class ProviderTestResult(BaseModel):
    ok: bool
    detail: str = ""
    latency_ms: int = 0
