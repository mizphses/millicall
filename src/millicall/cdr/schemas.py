from datetime import datetime

from pydantic import BaseModel, ConfigDict


class CdrRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    call_uuid: str
    direction: str
    src_number: str
    dst_number: str
    caller_id_name: str
    started_at: datetime | None
    answered_at: datetime | None
    ended_at: datetime | None
    duration_seconds: int
    billsec_seconds: int
    hangup_cause: str
