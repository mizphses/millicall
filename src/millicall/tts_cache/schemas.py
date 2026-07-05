from pydantic import BaseModel, Field


class SynthesizeRequest(BaseModel):
    provider_id: int
    text: str = Field(..., min_length=1, max_length=2000)


class SynthesizeResult(BaseModel):
    path: str
    cached: bool
