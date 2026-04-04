from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    email: str = Field(..., max_length=320)
    password: str = Field(..., min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: str
    email: str
    full_name: str
    organization_id: str
    is_active: bool

    model_config = {"from_attributes": True}
