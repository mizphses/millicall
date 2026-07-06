from datetime import datetime

from pydantic import BaseModel, ConfigDict


class CallMessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    call_uuid: str
    agent_id: int | None
    role: str
    text: str
    latency_ms: int | None
    created_at: datetime
