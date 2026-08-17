"""Schemas for organization settings."""

import re

from pydantic import BaseModel, Field, field_validator

# 3- or 6-digit hex color, with the leading '#'. White-label accent tokens are
# injected verbatim into CSS custom properties on document.documentElement, so
# we constrain them to a strict color literal — no `url(...)`, no `expression()`,
# nothing that could smuggle a value into the cascade.
_HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

# Accept only http(s) URLs for the logo / support / legal links. A logo_url is
# rendered as an <img src> and the links as <a href>, so we reject javascript:,
# data:, and other schemes outright.
_URL_RE = re.compile(r"^https?://", re.IGNORECASE)

# Defensive caps so a giant string can't bloat the settings JSONB.
_MAX_NAME = 120
_MAX_URL = 2048


class CompanyProfile(BaseModel):
    address: str = ""
    phone: str = ""
    website: str = ""
    tax_id: str = ""
    logo_url: str = ""


class InvoiceDefaults(BaseModel):
    currency: str = "USD"
    payment_terms: str = "Net 30"
    number_prefix: str = "INV-"
    default_gl_account: str = ""
    default_cost_center: str = ""


class OrganizationSettings(BaseModel):
    company: CompanyProfile = Field(default_factory=CompanyProfile)
    invoice_defaults: InvoiceDefaults = Field(default_factory=InvoiceDefaults)


class BrandConfig(BaseModel):
    """Per-tenant white-label branding (stored under `settings.brand`).

    All fields optional / empty by default — an unset field means "use the
    platform default" (product name "FeohLedger", the bundled logo, the
    app.css accent tokens). Validated so the values are safe to inject into the
    DOM: accent colors must be hex literals, URLs must be http(s).
    """

    product_name: str = Field(default="", max_length=_MAX_NAME)
    logo_url: str = Field(default="", max_length=_MAX_URL)
    accent_color: str = ""  # primary accent (borders, focus rings, accent text)
    accent_strong_color: str = ""  # darker companion for accent BACKGROUNDS (AA text)
    support_url: str = Field(default="", max_length=_MAX_URL)
    legal_url: str = Field(default="", max_length=_MAX_URL)

    @field_validator("accent_color", "accent_strong_color")
    @classmethod
    def _validate_hex(cls, v: str) -> str:
        v = (v or "").strip()
        if v and not _HEX_COLOR_RE.match(v):
            raise ValueError("must be a 3- or 6-digit hex color (e.g. #638cff)")
        return v

    @field_validator("logo_url", "support_url", "legal_url")
    @classmethod
    def _validate_url(cls, v: str) -> str:
        v = (v or "").strip()
        if v and not _URL_RE.match(v):
            raise ValueError("must be an http(s) URL")
        return v

    @field_validator("product_name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        return (v or "").strip()


class CustomDomainsConfig(BaseModel):
    """The tenant's registered white-label vanity hostnames.

    Stored under `settings.brand.custom_domains` (a JSON array of bare,
    lowercase hostnames). A request arriving on one of these hosts with no
    `X-Tenant-Slug` header resolves to this tenant — but only as a *candidate*;
    the JWT `org`-claim cross-check in `app.tenant.get_tenant` still gates it
    (see `docs/white-label.md` § Custom domains). Validation/normalization is
    done in the endpoint via the SAME `normalize_custom_domain` the resolver
    uses — not here — so the stored value can never diverge from what resolves.
    """

    custom_domains: list[str] = Field(default_factory=list)


class ChatNotificationStatus(BaseModel):
    """The **credential-free** view of `settings.chat_notifications`.

    `webhook_url` is deliberately absent and always will be: it is a bearer
    capability (see `services/chat_notifications_config`), so it is write-only.
    `webhook_host` is the bare hostname — enough for an admin to answer "where
    does our approval channel post?" during an incident, with none of the path
    or query that carries the provider's token.
    """

    enabled: bool = False
    provider: str | None = None
    events: dict[str, bool] = Field(default_factory=dict)
    webhook_configured: bool = False
    webhook_host: str | None = None
    # Registry-derived, so the picker can't offer a provider with no adapter.
    supported_providers: list[str] = Field(default_factory=list)
    supported_events: list[str] = Field(default_factory=list)


class UpdateChatNotificationsRequest(BaseModel):
    """Non-credential chat settings. The webhook URL is NOT settable here — it
    has its own endpoint, and a save through this one preserves it."""

    enabled: bool = False
    provider: str = "mock"
    events: dict[str, bool] = Field(default_factory=dict)


class SetChatWebhookRequest(BaseModel):
    """The incoming-webhook URL (the credential).

    Deliberately declared as a bare `str` with **no** Pydantic constraints:
    FastAPI's default `RequestValidationError` body echoes the offending
    `input` back, so a `min_length` / pattern failure here would put the
    credential into an HTTP error body (and any log or APM span that captures
    one). Every check runs in the endpoint instead and answers with one
    generic, value-free 422.
    """

    webhook_url: str = ""


class OrganizationResponse(BaseModel):
    id: str
    name: str
    slug: str
    plan: str
    settings: dict  # raw JSONB — preserves erp, cards, extraction keys
    created_at: str


class UpdateOrganizationRequest(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    settings: dict | None = None  # raw dict — merged into existing settings
