"""Email-to-invoice intake.

Receives forwarded-PDF invoices from a per-tenant inbound email address
(e.g. ``invoices+a1b2c3d4@ap.yourcompany.com``) and routes each
attachment through the normal extraction pipeline. The AP team tells
their vendors "send POs to this address" and the invoice appears in the
queue without anyone touching the UI.

Public API:
    - ``provision_intake_token(org)`` — generate+persist a token on the org
    - ``resolve_tenant_from_recipient(ctrl_db, to_address)`` — reverse-lookup
    - ``process_inbound_email(ctrl_db, payload)`` — the webhook entry point

The webhook endpoint is provider-agnostic. We accept a normalized
:class:`InboundEmail` payload; provider-specific parsers live in
``services/email_intake_adapters/`` (SES SNS JSON, Mailgun form-data, etc).
This keeps the core intake logic testable and the provider plumbing
isolated.

Security:
    - Token in the recipient address is the tenant bearer — treat it like
      a password. Leaked token = spam channel into that tenant's AP queue.
    - HMAC-SHA256 signature verification against
      ``settings.email_intake_signing_secret`` when the header is present.
    - We silently drop attachments that are not PDFs / images (avoid
      shipping .docx Trojans into the extraction pipeline).
    - Rate-limiting is the provider's job — point SES at a Lambda that
      drops duplicates before they reach us, or use Mailgun's built-in
      rate limits.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.invoice import Invoice, InvoiceStatus
from app.models.organization import Organization

logger = logging.getLogger(__name__)

# Nil UUID sentinel — email intake has no human actor, so every audit trail
# written during intake points at this ID. The UI can translate it to "system
# (email)" when rendering.
SYSTEM_ACTOR_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")

_ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/tiff",
}


# ---------------------------------------------------------------------------
# Normalized payload shape — what every provider adapter emits
# ---------------------------------------------------------------------------


@dataclass
class InboundAttachment:
    filename: str
    content_type: str
    content: bytes  # decoded bytes; adapters handle base64/MIME-decoding


@dataclass
class InboundEmail:
    to: str  # the recipient address that hit our MX
    sender: str
    subject: str = ""
    message_id: str = ""
    received_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    attachments: list[InboundAttachment] = field(default_factory=list)


@dataclass
class IntakeResult:
    tenant_slug: str | None = None
    invoices_created: list[uuid.UUID] = field(default_factory=list)
    skipped_attachments: list[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "tenant_slug": self.tenant_slug,
            "invoices_created": [str(x) for x in self.invoices_created],
            "skipped_attachments": self.skipped_attachments,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# Token management
# ---------------------------------------------------------------------------


def generate_intake_token(length: int = 16) -> str:
    """Cryptographically-random, URL-safe token used in the recipient address."""
    return secrets.token_urlsafe(length)[:length]


def provision_intake_token(org: Organization) -> str:
    """Assign a fresh intake token to an org (caller persists via db.commit())."""
    token = generate_intake_token()
    settings_ = dict(org.settings or {})
    intake = dict(settings_.get("email_intake") or {})
    intake["token"] = token
    intake["enabled"] = True
    intake["rotated_at"] = datetime.now(UTC).isoformat()
    settings_["email_intake"] = intake
    org.settings = settings_
    return token


def intake_address_for(org: Organization) -> str | None:
    """Render the public intake address, or None if not provisioned / configured."""
    domain = settings.email_intake_domain
    if not domain:
        return None
    token = ((org.settings or {}).get("email_intake") or {}).get("token")
    if not token:
        return None
    return f"invoices+{token}@{domain}"


# ---------------------------------------------------------------------------
# Recipient → tenant resolution
# ---------------------------------------------------------------------------


_PLUS_TOKEN_RE = re.compile(r"invoices\+([A-Za-z0-9_-]+)@", re.IGNORECASE)


def extract_token(to_address: str) -> str | None:
    """Parse the ``+<token>@`` piece out of the recipient address."""
    match = _PLUS_TOKEN_RE.search(to_address or "")
    return match.group(1) if match else None


async def resolve_tenant_from_recipient(
    ctrl_db: AsyncSession,
    to_address: str,
) -> Organization | None:
    """Look up the organization whose intake token matches the recipient address."""
    token = extract_token(to_address)
    if not token:
        return None

    # Postgres JSONB containment — the index-friendly form is
    # `settings @> '{"email_intake":{"token":"..."}}'` but we avoid a raw
    # fragment here and filter in Python on the small org set. For 10k
    # orgs this is still a single round-trip; revisit if it matters.
    q = await ctrl_db.execute(select(Organization))
    for org in q.scalars().all():
        intake = (org.settings or {}).get("email_intake") or {}
        if intake.get("enabled") and intake.get("token") == token:
            return org
    return None


# ---------------------------------------------------------------------------
# HMAC verification (optional — when the provider signs the body)
# ---------------------------------------------------------------------------


def verify_signature(body: bytes, signature: str | None) -> bool:
    """Verify an HMAC-SHA256 signature of the webhook body.

    Fails closed: returns ``False`` whenever the secret is empty unless
    ``AP_DEBUG`` is true (local dev convenience). The startup guard in
    :func:`main.lifespan` already refuses to boot a deployed env that has
    ``email_intake_domain`` set but ``email_intake_signing_secret`` empty,
    so this branch only fires for an explicitly debug-mode developer.
    """
    secret = settings.email_intake_signing_secret
    if not secret:
        return bool(settings.debug)
    if not signature:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


# ---------------------------------------------------------------------------
# Processing
# ---------------------------------------------------------------------------


async def process_inbound_email(
    ctrl_db: AsyncSession,
    payload: InboundEmail,
) -> IntakeResult:
    """Route an inbound email to the right tenant and create invoices.

    Returns :class:`IntakeResult` — the caller (webhook endpoint) returns
    it verbatim to the email provider for debugging.
    """
    result = IntakeResult()

    org = await resolve_tenant_from_recipient(ctrl_db, payload.to)
    if org is None:
        result.error = "Unknown or disabled intake address"
        logger.warning("Email intake: unresolved recipient %r", payload.to)
        return result

    result.tenant_slug = org.slug
    attachments = list(_usable_attachments(payload.attachments, result))
    if not attachments:
        result.error = "No usable PDF / image attachments"
        return result

    # Open a short-lived tenant session to create the invoice rows.
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.database import _make_tenant_url
    from app.services.extraction_dispatch import dispatch_extraction
    from app.services.storage import _ensure_bucket, _get_client

    tenant_engine = create_async_engine(_make_tenant_url(org.db_name), pool_size=1, max_overflow=0)
    tenant_factory = async_sessionmaker(tenant_engine, expire_on_commit=False)

    s3 = _get_client()
    _ensure_bucket(s3)

    try:
        async with tenant_factory() as tenant_db:
            for att in attachments:
                invoice_id = await _create_invoice_from_attachment(
                    tenant_db=tenant_db,
                    org_id=org.id,
                    sender=payload.sender,
                    subject=payload.subject,
                    attachment=att,
                    s3=s3,
                )
                result.invoices_created.append(invoice_id)
            await tenant_db.commit()

        # Dispatch extraction OUTSIDE the tenant transaction so failures
        # here don't roll back the invoice rows.
        for invoice_id in result.invoices_created:
            await dispatch_extraction(invoice_id, org.id, SYSTEM_ACTOR_ID)
    finally:
        await tenant_engine.dispose()

    return result


def _usable_attachments(
    attachments: Iterable[InboundAttachment],
    result: IntakeResult,
) -> Iterable[InboundAttachment]:
    for att in attachments:
        ct = (att.content_type or "").lower()
        if ct not in _ALLOWED_CONTENT_TYPES:
            result.skipped_attachments.append(f"{att.filename} ({ct or 'unknown'})")
            continue
        if not att.content:
            result.skipped_attachments.append(f"{att.filename} (empty)")
            continue
        yield att


async def _create_invoice_from_attachment(
    *,
    tenant_db: AsyncSession,
    org_id: uuid.UUID,
    sender: str,
    subject: str,
    attachment: InboundAttachment,
    s3,
) -> uuid.UUID:
    invoice = Invoice(
        invoice_number="",
        vendor_name="",
        description=f"Received by email from {sender}" + (f" — {subject}" if subject else ""),
        amount=Decimal("0"),
        currency="USD",
        status=InvoiceStatus.pending,  # skip 'new' — intake = trigger extraction
        organization_id=org_id,
        uploaded_by_id=None,  # system — no human uploader
    )
    tenant_db.add(invoice)
    await tenant_db.flush()

    from app.services.storage import _safe_filename

    file_key = f"{org_id}/{invoice.id}/{_safe_filename(attachment.filename)}"
    s3.put_object(
        Bucket=settings.s3_bucket,
        Key=file_key,
        Body=attachment.content,
        ContentType=attachment.content_type,
    )
    invoice.file_key = file_key
    invoice.file_url = f"{settings.s3_endpoint_url.rstrip('/')}/{settings.s3_bucket}/{file_key}"
    return invoice.id
