"""Helpers for issuing virtual cards.

Used by both the explicit `/api/cards/generate` endpoint and the
payment-run executor when a row's method is `virtual_card`. Keeping the
provider-call shape in one place means a future card adapter only has to
change the inputs in one spot.

This module is sync-call-friendly: every helper either returns a row or
raises. Callers are responsible for awaiting `db.flush` / `db.commit`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice import Invoice
from app.models.vendor import Vendor
from app.models.virtual_card import VirtualCard

logger = logging.getLogger(__name__)


@dataclass
class CardIssueResult:
    """Returned to the payment-run executor so it can record provenance
    on the matching Payment row."""

    card: VirtualCard | None
    success: bool
    failure_reason: str | None = None


DEFAULT_CARD_EXPIRY_DAYS = 30


def _coerce_expiry_days(raw) -> int:
    """Coerce an org-supplied `default_expiry_days` into a sane int.

    Tenant settings come from a JSONB column, so the value could be a
    string like "14" pasted into an admin form, a float, or garbage.
    Anything that doesn't reduce to a positive int falls back to the
    platform default. Returning a bogus expiry here would either mint
    cards that expired in the past or cards that never expire.
    """
    if raw is None:
        return DEFAULT_CARD_EXPIRY_DAYS
    try:
        days = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_CARD_EXPIRY_DAYS
    if days <= 0:
        return DEFAULT_CARD_EXPIRY_DAYS
    return days


def _resolve_card_config(org_settings: dict, app_settings) -> dict | None:
    """Mirror of `app.api.cards._resolve_card_config` but pure — no
    Organization model, no FastAPI deps. Returns None if the org hasn't
    enabled cards."""
    org_cards = (org_settings or {}).get("cards") or {}
    if not org_cards.get("enabled"):
        return None

    program_type = org_cards.get("program_type", "platform")
    region = org_cards.get("region", "US")
    expiry_days = _coerce_expiry_days(org_cards.get("default_expiry_days"))

    if program_type == "byok":
        return {
            "provider": org_cards.get("provider", ""),
            "region": region,
            "api_key": org_cards.get("api_key", ""),
            "client_id": org_cards.get("client_id", ""),
            "client_secret": org_cards.get("client_secret", ""),
            "customer_hash_id": org_cards.get("customer_hash_id", ""),
            "wallet_hash_id": org_cards.get("wallet_hash_id", ""),
            # BYOK sandbox is opt-IN — a customer supplying their own real keys
            # expects live rails; defaulting to sandbox silently paid invoices
            # into the provider's sandbox. Mirror api.cards._resolve_card_config.
            "sandbox": org_cards.get("sandbox", False),
            "default_expiry_days": expiry_days,
        }

    from app.services.card_adapters.dispatcher import get_default_provider

    provider = get_default_provider(region)
    if provider == "lithic":
        return {
            "provider": "lithic",
            "region": region,
            "api_key": app_settings.lithic_api_key,
            "sandbox": app_settings.lithic_sandbox,
            "default_expiry_days": expiry_days,
        }
    return {
        "provider": "nium",
        "region": region,
        "client_id": app_settings.nium_client_id,
        "client_secret": app_settings.nium_client_secret,
        "customer_hash_id": app_settings.nium_customer_hash_id,
        "wallet_hash_id": app_settings.nium_wallet_hash_id,
        "sandbox": app_settings.nium_sandbox,
        "default_expiry_days": expiry_days,
    }


async def issue_card_for_invoice(
    *,
    invoice: Invoice,
    organization_id,
    org_settings: dict,
    app_settings,
    payment_id=None,
    amount: Decimal | None = None,
) -> CardIssueResult:
    """Mint one card via the org's configured adapter and persist the
    VirtualCard row. Returns the row (uncommitted — caller flushes).

    Returns success=False with a populated failure_reason when:
      - The org hasn't enabled cards (`cards.enabled` falsy)
      - The adapter call fails

    Caller decides what to do on failure (skip the row, downgrade to
    ACH, etc.). This module never raises on a known business condition.
    """
    config = _resolve_card_config(org_settings, app_settings)
    if config is None:
        return CardIssueResult(card=None, success=False, failure_reason="cards_not_enabled")

    # Ensure the adapter classes register themselves with the dispatcher.
    import app.services.card_adapters.lithic  # noqa: F401
    import app.services.card_adapters.mock_adapter  # noqa: F401
    import app.services.card_adapters.nium  # noqa: F401
    from app.services.card_adapters import VirtualCardPayload, get_card_adapter

    adapter = get_card_adapter(config)
    expiry_days = config.get("default_expiry_days", DEFAULT_CARD_EXPIRY_DAYS)

    payload = VirtualCardPayload(
        correlation_id=str(invoice.correlation_id) if invoice.correlation_id else "",
        invoice_id=str(invoice.id),
        vendor_name=invoice.vendor_name,
        vendor_email=None,
        amount=amount or invoice.amount,
        currency=invoice.currency or "USD",
        description=invoice.description,
        expiry_days=expiry_days,
    )

    try:
        result = await adapter.create_card(payload)
    except Exception as exc:  # noqa: BLE001
        # Log the exception type, never the message. A card-provider
        # adapter could surface a partial PAN / merchant token in its
        # error string; interpolating `exc` would push that into the
        # log sink (invariant #7).
        logger.warning(
            "[card_issuance] adapter %s raised on invoice %s: %s",
            adapter.provider_name,
            invoice.id,
            exc.__class__.__name__,
        )
        return CardIssueResult(
            card=None, success=False, failure_reason=f"adapter_error:{exc.__class__.__name__}"
        )

    if not result.success:
        return CardIssueResult(
            card=None,
            success=False,
            failure_reason=result.failure_reason or "adapter_returned_failure",
        )

    card = VirtualCard(
        invoice_id=invoice.id,
        payment_id=payment_id,
        vendor_id=invoice.vendor_id,
        correlation_id=invoice.correlation_id,
        card_provider=adapter.provider_name,
        provider_card_id=result.provider_card_id or "",
        last_four=result.last_four,
        amount_limit=amount or invoice.amount,
        currency=invoice.currency or "USD",
        status="created",
        expires_at=datetime.now(UTC) + timedelta(days=expiry_days),
        organization_id=organization_id,
    )
    return CardIssueResult(card=card, success=True)


async def notify_vendor_of_card(
    db: AsyncSession,
    *,
    card: VirtualCard,
    invoice: Invoice,
    org_name: str,
    org_slug: str,
    public_url_template: str | None,
) -> bool:
    """Mint a single-use reveal token, send the vendor an email with
    the link. Returns True if the email was dispatched, False if we
    skipped (no vendor email, no template, or adapter error).

    Fail-soft: a missing vendor email or a flaky email adapter must not
    block the card issuance flow. The card is already minted; the email
    is a courtesy.
    """
    if not invoice.vendor_id:
        return False

    vendor_result = await db.execute(select(Vendor).where(Vendor.id == invoice.vendor_id))
    vendor = vendor_result.scalar_one_or_none()
    if vendor is None or not vendor.email:
        logger.info(
            "[card_issuance] skipping vendor email — vendor %s has no email on file",
            invoice.vendor_id,
        )
        return False

    from app.services.card_reveal import mint_reveal_token

    plaintext_token = await mint_reveal_token(db, card)

    # `public_url_template` is the same `AP_TENANT_URL_TEMPLATE` the
    # signup flow uses ("https://{slug}.app.com"). When empty, we skip
    # the email — nothing useful to point the vendor at.
    if not public_url_template:
        return False
    base = public_url_template.replace("{slug}", org_slug)
    link = f"{base.rstrip('/')}/portal/cards/{plaintext_token}"

    subject = f"{org_name} sent you a virtual card payment"
    body_text = (
        f"Hi {vendor.name},\n\n"
        f"{org_name} has issued a virtual card to pay invoice "
        f"{invoice.invoice_number or invoice.id} for "
        f"{invoice.currency or 'USD'} {(card.amount_limit):,.2f}.\n\n"
        f"View your one-time card details:\n  {link}\n\n"
        "This link will expire in 7 days and can only be used once. "
        "If you didn't expect this email, please reply to let us know.\n"
    )
    body_html = (
        f"<p>Hi {vendor.name},</p>"
        f"<p><strong>{org_name}</strong> has issued a virtual card to pay invoice "
        f"<code>{invoice.invoice_number or invoice.id}</code> for "
        f"<strong>{invoice.currency or 'USD'} {(card.amount_limit):,.2f}</strong>.</p>"
        f'<p><a href="{link}">View your one-time card details</a></p>'
        f"<p style='color:#888;font-size:12px'>This link expires in 7 days "
        "and can only be used once.</p>"
    )

    from app.services.email_adapters import EmailMessage, get_email_adapter

    try:
        adapter = get_email_adapter()
        await adapter.send(
            EmailMessage(
                to=vendor.email,
                subject=subject,
                body_text=body_text,
                body_html=body_html,
            )
        )
        return True
    except Exception as exc:  # noqa: BLE001
        # PII guard: the raw exception can embed the vendor's email address
        # (SES/SMTP echo the recipient in the error). Log the type only.
        logger.warning(
            "[card_issuance] vendor email send failed for card %s: %s",
            card.id,
            exc.__class__.__name__,
        )
        return False
