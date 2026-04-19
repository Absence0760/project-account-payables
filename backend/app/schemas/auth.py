from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    email: str = Field(..., max_length=320)
    password: str = Field(..., min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    must_change_password: bool = False


class MFAChallengeResponse(BaseModel):
    """Returned by /auth/login when the password checks out but the user still
    has to clear MFA. The browser swaps `mfa_challenge_token` for an
    access_token by calling /auth/mfa/verify."""

    mfa_required: bool = True
    mfa_challenge_token: str
    methods: list[str]  # ["totp", "email"] — what the user can submit
    must_enroll: bool = False  # True when org enforces MFA but user isn't enrolled


class UserResponse(BaseModel):
    id: str
    email: str
    full_name: str
    organization_id: str
    is_active: bool
    must_change_password: bool = False
    mfa_enabled: bool = False
    mfa_required_by_org: bool = False
    roles: list[str] = []

    model_config = {"from_attributes": True}


class MFAEnrollStartResponse(BaseModel):
    """First step of TOTP enrollment — server mints a secret + QR. The secret
    is also returned in plaintext so users with no QR scanner can paste it
    into their authenticator app manually."""

    secret: str
    provisioning_uri: str
    qr_code_data_url: str


class MFAEnrollVerifyRequest(BaseModel):
    code: str = Field(..., min_length=6, max_length=8)


class MFADisableRequest(BaseModel):
    """Disabling MFA requires re-confirming the password — defense against
    a stolen-session takeover that turns off MFA without anyone noticing."""

    password: str = Field(..., min_length=1)


class MFAVerifyRequest(BaseModel):
    challenge_token: str = Field(..., min_length=1)
    code: str = Field(..., min_length=6, max_length=8)
    method: str = Field(..., pattern="^(totp|email)$")


class MFAEmailChallengeRequest(BaseModel):
    challenge_token: str = Field(..., min_length=1)


class UpdateProfileRequest(BaseModel):
    full_name: str | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, min_length=6)
    current_password: str | None = Field(default=None, min_length=1)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=12, max_length=128)
