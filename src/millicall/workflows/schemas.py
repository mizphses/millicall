"""API request/response models for the workflows CRUD + AI-generate endpoints
(Phase 4b Task 2).

The graph ``definition`` is accepted/returned as a raw dict here; strict typed
validation happens in the router via ``WorkflowDefinition.model_validate`` +
``validate_graph`` (Task 1), so the persisted JSON round-trips GUI-only keys.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class WorkflowUpsert(BaseModel):
    """Body for POST (create) and PUT (full replace)."""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(..., min_length=1, max_length=100)
    number: str = Field(..., pattern=r"^[0-9*#]{1,30}$")
    description: str = ""
    default_tts_provider_id: int | None = None
    enabled: bool = True
    definition: dict[str, Any] = Field(default_factory=lambda: {"nodes": [], "edges": []})


class WorkflowRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    number: str
    description: str
    default_tts_provider_id: int | None
    enabled: bool
    definition: dict[str, Any]
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class WorkflowGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    prompt: str = Field(..., min_length=1)


class WorkflowGenerateResponse(BaseModel):
    definition: dict[str, Any]
    warnings: list[str] = Field(default_factory=list)
