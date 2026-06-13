from pydantic import BaseModel, Field


class EntityCreate(BaseModel):
    name: str = Field(..., max_length=255)
    slug: str = Field(..., max_length=100)
    # ISO 4217 (3 letters). None → use the org's reporting currency.
    currency: str | None = Field(default=None, min_length=3, max_length=3)


class EntityUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    is_active: bool | None = None
