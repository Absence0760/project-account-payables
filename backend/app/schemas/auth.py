from datetime import datetime

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
    # The user's effective granular permissions — the union over their roles
    # (system roles via the static default map, custom roles via their stored
    # list). Drives the frontend `can(perm)` gate for the split sensitive
    # controls; `roles` still drives everything not yet migrated to permissions.
    permissions: list[str] = []
    # Account-level email-language preference (NULL = English fallback). Drives
    # outbound email copy only — NOT in-app UI (the frontend's per-device locale
    # picker owns that). See docs/notifications.md § Localized email.
    locale: str | None = None

    model_config = {"from_attributes": True}


class MFAEnrollStartResponse(BaseModel):
    """First step of TOTP enrollment — server mints a secret + QR. The secret
    is also returned in plaintext so users with no QR scanner can paste it
    into their authenticator app manually."""

    secret: str
    provisioning_uri: str
    qr_code_data_url: str


class MFAStepUpRequest(BaseModel):
    """Optional re-authentication sent when *changing* an account's second
    factor (starting a fresh TOTP enrollment, registering or deleting a
    passkey, disabling TOTP).

    Every field is optional because first-time enrollment stays frictionless:
    an account with no factor yet has nothing to protect. Once a factor IS in
    force, one of the three must be supplied and check out — the account
    password, a code from the currently enrolled authenticator, or a WebAuthn
    `assertion` from an already-registered passkey. Otherwise a stolen access
    token alone would be enough to swap the second factor.

    `assertion` is the browser's `navigator.credentials.get()` response for a
    challenge minted by `POST /auth/mfa/step-up/passkey` for this exact
    operation. It is the only proof a passwordless SSO-only account can offer.
    """

    password: str | None = Field(default=None, min_length=1)
    code: str | None = Field(default=None, min_length=6, max_length=8)
    assertion: dict | None = Field(default=None)


class MFAEnrollVerifyRequest(BaseModel):
    code: str = Field(..., min_length=6, max_length=8)


class MFADisableRequest(MFAStepUpRequest):
    """Disabling MFA is a factor change like any other, so it takes the same
    step-up proofs — password, a code from the current authenticator, or a
    passkey assertion. Defense against a stolen-session takeover that turns off
    MFA without anyone noticing.

    Kept as its own name (rather than reusing `MFAStepUpRequest` directly) so
    the route reads intelligibly and the OpenAPI schema stays descriptive; it
    adds no fields. Password is optional here for the same reason it is on the
    parent: an SSO-only account has none, and its passkey assertion is the
    proof instead.
    """


class MFAVerifyRequest(BaseModel):
    challenge_token: str = Field(..., min_length=1)
    code: str = Field(..., min_length=6, max_length=8)
    method: str = Field(..., pattern="^(totp|email)$")


# ---- WebAuthn / passkeys (additional MFA factor) ------------------------


class WebAuthnRegisterStartResponse(BaseModel):
    """First step of passkey enrollment — server-minted
    ``navigator.credentials.create()`` options, already in the WebAuthn wire
    shape. The frontend passes ``options`` straight to the browser API."""

    options: dict


class WebAuthnRegisterFinishRequest(BaseModel):
    """The browser's ``create()`` response, serialized. ``credential`` is the
    JSON the WebAuthn API returns; ``name`` labels the passkey in the UI."""

    credential: dict
    name: str = Field(default="Passkey", max_length=120)


class WebAuthnCredentialResponse(BaseModel):
    """A registered passkey, metadata only — never the public key or counter."""

    id: str
    name: str
    transports: str | None = None
    created_at: str | None = None
    last_used_at: str | None = None


class WebAuthnAuthStartRequest(BaseModel):
    """Begin a passkey login challenge. ``challenge_token`` is the same
    short-lived MFA challenge JWT minted by /auth/login."""

    challenge_token: str = Field(..., min_length=1)


class WebAuthnAuthStartResponse(BaseModel):
    """Server-minted ``navigator.credentials.get()`` options."""

    options: dict


class WebAuthnAuthFinishRequest(BaseModel):
    challenge_token: str = Field(..., min_length=1)
    credential: dict


# The closed set of factor-management operations a step-up can authorize. A
# step-up assertion is bound to exactly one of these (it selects the Redis
# challenge slot), so an assertion obtained for one can't be replayed against
# another. Keep in lockstep with the `operation=` literals passed to
# `api/auth._require_mfa_step_up`.
STEP_UP_OPERATIONS = ("totp_enroll", "totp_disable", "passkey_register", "passkey_delete")


class WebAuthnStepUpStartRequest(BaseModel):
    """Begin a passkey STEP-UP challenge (authenticated — the caller already
    holds an access token; this re-proves they hold the authenticator too).

    `operation` names what the resulting assertion will be allowed to
    authorize. It is part of the challenge's identity, not a hint: the server
    stashes the challenge under it and the mutating endpoint looks it up under
    its own operation, so a mismatch fails.
    """

    operation: str = Field(
        ..., pattern="^(totp_enroll|totp_disable|passkey_register|passkey_delete)$"
    )


class MFAEmailChallengeRequest(BaseModel):
    challenge_token: str = Field(..., min_length=1)


class UpdateProfileRequest(BaseModel):
    full_name: str | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, min_length=6)
    current_password: str | None = Field(default=None, min_length=1)
    # Email-language preference. Validated against the supported locale set at
    # the route (422 on an unknown value); empty string clears it (→ English).
    locale: str | None = Field(default=None, max_length=16)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=12, max_length=128)


class SessionResponse(BaseModel):
    """One of the caller's own live sign-ins.

    `id` is the token's JTI — an opaque session handle, not a credential (the
    JWT itself never leaves the client that holds it), and the only thing the
    revoke endpoint accepts. `ip` / `device` / `method` are best-effort: a
    session recorded before this shipped, or one from a client that sent no
    User-Agent, simply reports nulls rather than a guess.
    """

    id: str
    created_at: datetime
    expires_at: datetime
    ip: str | None = None
    device: str | None = None
    method: str | None = None
    # True for the session making this request — the one the UI must not
    # invite the user to kill by accident.
    current: bool = False


class SessionRevokeResponse(BaseModel):
    """How many sessions the revoke actually ended."""

    revoked: int
