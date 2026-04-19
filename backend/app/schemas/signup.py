from pydantic import BaseModel, Field


class SignupStartRequest(BaseModel):
    company_name: str = Field(..., min_length=2, max_length=255)
    slug: str = Field(..., min_length=3, max_length=30)
    admin_name: str = Field(..., min_length=2, max_length=255)
    admin_email: str = Field(..., min_length=3, max_length=320)
    # hCaptcha response token from the widget.
    captcha_token: str | None = None


class SignupStartResponse(BaseModel):
    status: str = "verification_email_sent"
    message: str


class SignupCompleteRequest(BaseModel):
    token: str = Field(..., min_length=16, max_length=64)


class SignupCompleteResponse(BaseModel):
    status: str = "provisioned"
    slug: str
    tenant_url: str
    admin_email: str


class SlugCheckResponse(BaseModel):
    slug: str
    available: bool
    reason: str | None = None
