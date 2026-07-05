from pydantic import BaseModel, ConfigDict, Field


class AiAgentCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(..., min_length=1, max_length=100)
    system_prompt: str = ""
    greeting: str = ""
    llm_provider_id: int
    tts_provider_id: int
    stt_provider_id: int
    max_history: int = Field(default=10, ge=1, le=50)
    silence_end_ms: int = Field(default=600, ge=200, le=3000)
    enabled: bool = True


class AiAgentUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str | None = Field(default=None, min_length=1, max_length=100)
    system_prompt: str | None = None
    greeting: str | None = None
    llm_provider_id: int | None = None
    tts_provider_id: int | None = None
    stt_provider_id: int | None = None
    max_history: int | None = Field(default=None, ge=1, le=50)
    silence_end_ms: int | None = Field(default=None, ge=200, le=3000)
    enabled: bool | None = None


class AiAgentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    system_prompt: str
    greeting: str
    llm_provider_id: int
    tts_provider_id: int
    stt_provider_id: int
    max_history: int
    silence_end_ms: int
    enabled: bool
