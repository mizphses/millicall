from pydantic import BaseModel, ConfigDict, Field


class ContactCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(..., min_length=1, max_length=100)
    phone_number: str = Field(..., min_length=1, max_length=30)
    company: str = Field(default="", max_length=100)
    department: str = Field(default="", max_length=100)
    notes: str = Field(default="", max_length=2000)


class ContactUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str | None = Field(default=None, min_length=1, max_length=100)
    phone_number: str | None = Field(default=None, min_length=1, max_length=30)
    company: str | None = Field(default=None, max_length=100)
    department: str | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, max_length=2000)


class ContactRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    phone_number: str
    company: str
    department: str
    notes: str
