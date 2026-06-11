from pydantic import BaseModel, Field


class GLAccountCreate(BaseModel):
    code: str = Field(..., max_length=50)
    name: str = Field(..., max_length=255)
    account_type: str | None = None
    parent_code: str | None = None
