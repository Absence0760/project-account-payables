"""Request/response shapes for the supplier portal."""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.api.pagination import PageMeta


class PortalLoginRequest(BaseModel):
    email: str
    password: str


class PortalTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    must_change_password: bool = False


class PortalChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


# ---------- MFA (TOTP) — mirrors the employee MFA flow for vendor users ----------


class PortalMFAChallengeResponse(BaseModel):
    """Returned by /portal/auth/login when the password checks out but the
    vendor still has to clear MFA. The browser swaps `mfa_challenge_token` for
    an access token by calling /portal/auth/mfa/challenge. The challenge token
    carries `typ=vendor_mfa_challenge` — distinct from both the employee
    challenge (`mfa_challenge`) and the full vendor access token (`vendor`) — so
    a challenge token can never be used as an access token, and vice versa."""

    mfa_required: bool = True
    mfa_challenge_token: str
    methods: list[str] = ["totp"]  # TOTP only — no email-OTP backup for vendors yet


class PortalMFAEnrollStartResponse(BaseModel):
    """First step of TOTP enrollment — server mints a secret + QR. The secret is
    also returned in plaintext so vendors with no QR scanner can paste it into
    their authenticator app manually. Only returned during enrollment (before
    MFA is confirmed active); never echoed back afterwards."""

    secret: str
    provisioning_uri: str
    qr_code_data_url: str


class PortalMFAVerifyRequest(BaseModel):
    """Activate enrollment by proving the vendor can produce a valid code."""

    code: str = Field(..., min_length=6, max_length=8)


class PortalMFADisableRequest(BaseModel):
    """Disabling MFA re-verifies a current TOTP code — a stolen session
    shouldn't be able to silently strip MFA off."""

    code: str = Field(..., min_length=6, max_length=8)


class PortalMFAChallengeVerifyRequest(BaseModel):
    """Trade the login-issued challenge token + a valid TOTP code for a real
    vendor access token."""

    challenge_token: str = Field(..., min_length=1)
    code: str = Field(..., min_length=6, max_length=8)


class PortalMeResponse(BaseModel):
    id: str
    email: str
    full_name: str
    must_change_password: bool
    mfa_enabled: bool = False
    vendor_id: str
    vendor_name: str
    vendor_status: str


class PortalInviteRequest(BaseModel):
    email: str
    full_name: str


class PortalUserResponse(BaseModel):
    id: str
    vendor_id: str
    email: str
    full_name: str
    is_active: bool
    must_change_password: bool
    last_login_at: datetime | None = None
    created_at: datetime


class PortalInviteResponse(BaseModel):
    user: PortalUserResponse
    # The plaintext temp password is returned so the admin can share it
    # out-of-band if email delivery isn't configured (local dev, etc).
    # In production the welcome email also carries it.
    temp_password: str


class PortalInvoiceListItem(BaseModel):
    id: str
    invoice_number: str
    amount: Decimal
    currency: str
    status: str
    invoice_date: date | None = None
    due_date: date | None = None
    submitted_at: datetime
    file_url: str | None = None


class PortalInvoiceListResponse(PageMeta):
    items: list[PortalInvoiceListItem]
    total: int


class PortalPaymentListItem(BaseModel):
    id: str
    invoice_id: str
    invoice_number: str
    amount: Decimal
    method: str | None = None
    status: str
    reference: str | None = None
    submitted_at: datetime | None = None
    completed_at: datetime | None = None


class PortalPaymentListResponse(PageMeta):
    items: list[PortalPaymentListItem]
    total: int


# ---------- Purchase orders + PO flip ----------


class PortalPOListItem(BaseModel):
    id: str
    po_number: str
    status: str
    total: Decimal
    currency: str = "USD"
    line_item_count: int = 0
    created_at: datetime


class PortalPOListResponse(PageMeta):
    items: list[PortalPOListItem]
    total: int


class PortalPOLineItem(BaseModel):
    description: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    total: Decimal | None = None


class PortalPODetail(BaseModel):
    id: str
    po_number: str
    status: str
    total: Decimal
    currency: str = "USD"
    created_at: datetime
    line_items: list[PortalPOLineItem] = []


class PortalFlipResponse(BaseModel):
    id: str
    correlation_id: str
    status: str
    message: str


# ---------- Company self-service ----------


class PortalPendingChange(BaseModel):
    id: str
    change_type: str
    status: str
    created_at: datetime


class PortalCompanyInfoResponse(BaseModel):
    """Current company info as seen by the supplier.

    Never returns full bank account numbers or an unmasked tax ID — only
    a masked `tax_id_last4` and a `has_bank_details` boolean. The pending
    change (if any) lets the UI show a "pending AP approval" banner.
    """

    name: str
    email: str | None = None
    phone: str | None = None
    address: str | None = None
    tax_id_last4: str | None = None
    has_bank_details: bool = False
    pending_change: PortalPendingChange | None = None


class PortalCompanyInfoUpdateRequest(BaseModel):
    """The live-apply contact fields only. Bank details and tax ID are
    intentionally absent here — they route through the staging endpoints."""

    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=50)
    address: str | None = Field(default=None, max_length=500)


class PortalBankChangeRequest(BaseModel):
    bank_details: dict


class PortalTaxIdChangeRequest(BaseModel):
    tax_id: str = Field(..., min_length=1, max_length=50)


class PortalChangeRequestResponse(BaseModel):
    id: str
    change_type: str
    status: str
    created_at: datetime


# ---------------------------------------------------------------------------
# Supplier chat (portal side) — datetimes are raw `datetime`, matching the rest
# of portal.py. AP author ids are masked (never exposed to the supplier).
# See backend/docs/supplier-chat.md.
# ---------------------------------------------------------------------------


class PortalChatMessageCreate(BaseModel):
    body: str = Field(..., min_length=1, max_length=10_000)


class PortalChatAttachmentOut(BaseModel):
    file_url: str
    filename: str
    content_type: str
    size: int


class PortalChatMessageResponse(BaseModel):
    id: str
    author_role: str  # "ap_team" | "supplier" | "system"
    author_name: str | None  # NO author_user_id exposed to supplier
    body: str
    attachments: list[PortalChatAttachmentOut] = []
    created_at: datetime


class PortalChatThreadResponse(BaseModel):
    invoice_id: str
    status: str  # "open" | "resolved"
    messages: list[PortalChatMessageResponse] = []
