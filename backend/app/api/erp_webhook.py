"""ERP webhook endpoint — receives status callbacks from ERPs and Merge.dev."""

import logging
import uuid

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database import control_session_factory, get_tenant_engine
from app.models.exception import Exception as APException
from app.models.invoice import Invoice, InvoiceStatus
from app.models.organization import Organization
from app.services.exception_service import create_exception
from app.services.webhook_security import (
    extract_signature_header,
    is_event_already_processed,
    verify_hmac_sha256,
)
from app.services.workflow_engine import VALID_TRANSITIONS, transition_invoice

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/erp", tags=["erp"])

# Exception type opened when the ERP reports an invoice void/cancel we can no
# longer safely auto-apply (the invoice already advanced past the point where
# ``→ failed`` is a legal transition). Free-form ``Exception.exception_type``
# string — no migration. See the § Exception types list in backend/CLAUDE.md.
ERP_RECONCILIATION_EXCEPTION_TYPE = "erp_reconciliation"


# Map ERP status strings to our internal status transitions
ERP_STATUS_MAP = {
    # Merge.dev statuses
    "OPEN": InvoiceStatus.posted_in_erp,
    "SUBMITTED": InvoiceStatus.posted_in_erp,
    # Business Central
    "Open": InvoiceStatus.posted_in_erp,
    "Paid": InvoiceStatus.paid,
    # NetSuite
    "open": InvoiceStatus.posted_in_erp,
    "paidInFull": InvoiceStatus.paid,
    # Generic
    "posted": InvoiceStatus.posted_in_erp,
    "paid": InvoiceStatus.paid,
    "cancelled": InvoiceStatus.failed,
    "voided": InvoiceStatus.failed,
}


@router.post("/webhook/{erp_type}", status_code=status.HTTP_204_NO_CONTENT)
async def erp_webhook(
    erp_type: str,
    request: Request,
):
    """Receive status updates from ERPs or Merge.dev.

    Authenticated by HMAC over the raw body. The signing secret is
    looked up off the tenant named in the body — same pattern as the
    card webhook. Bad signatures, unknown tenants, missing events all
    return 204 silently. Leaking the distinction would help an
    attacker enumerate tenant slugs or replay events.

    Expected body:
    {
        "tenant_slug": "acme",
        "correlation_id": "uuid" | null,
        "erp_document_id": "string" | null,
        "event_id": "string" | null,
        "status": "Open" | "posted" | "paid" | ...,
        "details": { ... }  // optional extra data
    }

    For Merge.dev webhooks, the body structure may differ — we normalize it.
    """
    raw_body = await request.body()
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return  # malformed JSON → silent 204

    # Normalize — Merge.dev sends a different shape
    if erp_type == "merge_dev" and "data" in body:
        data = body["data"]
        body = {
            "tenant_slug": body.get("linked_account_id"),
            "correlation_id": data.get("integration_params", {}).get("correlation_id"),
            "erp_document_id": data.get("id"),
            "event_id": body.get("hook", {}).get("event") or body.get("event"),
            "status": data.get("status"),
            "details": data,
        }

    tenant_slug = body.get("tenant_slug")
    correlation_id = body.get("correlation_id")
    erp_document_id = body.get("erp_document_id")
    erp_status = body.get("status", "")
    # Deliberately NOT `body.get("event_id") or erp_document_id or
    # correlation_id`. Both fallbacks are constant for an invoice's WHOLE
    # lifecycle, so a direct integration that omits a per-delivery event_id
    # would have the first status event's dedup claim on that id silently
    # swallow every later distinct status event for the same invoice for the
    # rest of the dedup TTL (e.g. `posted_in_erp` claims it, the next day's
    # legitimate `paid` webhook never fires). `is_event_already_processed`
    # already has a real "missing event id -> always process" path — let a
    # genuinely absent event_id hit that instead of a fabricated one.
    event_id = body.get("event_id")

    if not tenant_slug:
        return  # silent — body didn't name a tenant
    if not correlation_id and not erp_document_id:
        return  # silent — nothing to look up

    # Look up the org. A missing slug → silent 204 (no enumeration).
    async with control_session_factory() as ctrl_db:
        result = await ctrl_db.execute(select(Organization).where(Organization.slug == tenant_slug))
        org = result.scalar_one_or_none()
        if not org:
            return

    # Verify HMAC against the tenant's configured signing secret.
    erp_config = (org.settings or {}).get("erp") or {}
    signing_secret = erp_config.get("webhook_signing_secret", "")
    if not signing_secret:
        # Fail closed (verify_hmac_sha256 would 204 on an empty secret anyway),
        # but surface a PII-free config error so an operator learns the ERP
        # integration is unconfigured rather than silently dropping every event.
        # tenant_slug / erp_type are non-PII identifiers; the secret is never logged.
        logger.warning(
            "ERP webhook dropped: no webhook_signing_secret configured for tenant '%s' (%s)",
            tenant_slug,
            erp_type,
        )
        return  # silent 204 to the caller (no enumeration)
    provided_sig = extract_signature_header(
        dict(request.headers),
        "X-Webhook-Signature",
        "X-Hub-Signature-256",
        "X-Merge-Webhook-Signature",
    )
    if not verify_hmac_sha256(signing_secret, raw_body, provided_sig):
        return  # silent 204 on bad / missing signature

    # Dedup by event id. Cross-tenant key because event ids should be
    # unique per provider; a duplicate within the TTL window is a
    # replay regardless of tenant.
    if await is_event_already_processed(f"erp:{erp_type}", str(event_id or "")):
        return

    # Map ERP status to our internal status
    target_status = ERP_STATUS_MAP.get(erp_status)
    if not target_status:
        return  # unknown status — silent ack

    # Open tenant DB and find the invoice
    engine = get_tenant_engine(org.db_name)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as db:
        try:
            query = select(Invoice)
            if correlation_id:
                query = query.where(Invoice.correlation_id == uuid.UUID(correlation_id))
            else:
                return  # erp_document_id-only path not supported yet

            result = await db.execute(query)
            invoice = result.scalar_one_or_none()

            if not invoice:
                return  # no matching invoice → silent ack

            # Only transition if the AUTHORITATIVE state machine permits it.
            # We deliberately do NOT keep a second local transition map here: a
            # divergent copy that permitted an edge VALID_TRANSITIONS forbids
            # (e.g. posted_in_erp → paid, or an ERP void/cancel → failed from
            # posted_in_erp / payment_scheduled) would make transition_invoice
            # raise a 409 that then escaped this handler — breaking the
            # documented "every webhook rejection path returns 204 silently"
            # contract. Screening against the canonical set guarantees the
            # webhook can never claim a transition the engine will reject, and a
            # transition the machine legitimately forbids becomes a silent ack
            # (same as an unknown status / unknown invoice), not a 409.
            current = invoice.status
            if target_status not in VALID_TRANSITIONS.get(current, set()):
                # The state machine forbids this transition for the invoice's
                # current state. Almost always a silent no-op — a stale or
                # duplicate ERP status for an invoice that already moved on.
                # ONE forbidden case is a real reconciliation signal we must
                # NOT drop: the ERP reports the invoice VOIDED/CANCELLED
                # (→ failed) after we already advanced it (sent_to_erp /
                # posted_in_erp / payment_scheduled / paid). Money may already
                # be in flight, so we never auto-transition (auto → failed from
                # payment_scheduled/paid would collide with the money path);
                # instead we open an Exception for a human to reconcile. Every
                # OTHER forbidden transition stays a pure silent no-op — turning
                # them all into exceptions would be noise.
                if target_status is InvoiceStatus.failed:
                    await _raise_erp_reconciliation_exception(
                        db,
                        invoice,
                        org_id=org.id,
                        erp_type=erp_type,
                        erp_status=erp_status,
                        erp_document_id=erp_document_id,
                        event_id=event_id,
                    )
                    await db.commit()
                return  # silent 204 on every forbidden-transition path

            await transition_invoice(
                db,
                invoice,
                target_status,
                action_name=f"invoice.erp_status_{target_status.value}",
                # PII guard: never splat the raw ERP `details` payload into the
                # append-only audit row — the ERP may include vendor bank/tax/address
                # fields. Whitelist only the safe, non-PII routing identifiers.
                details={
                    "erp_type": erp_type,
                    "erp_status": erp_status,
                    "erp_document_id": erp_document_id,
                    "raw_event_id": str(event_id) if event_id else None,
                },
            )
            await db.commit()
            return

        except HTTPException:
            # Defensive backstop: the VALID_TRANSITIONS guard above already
            # screens out every edge the state machine forbids, so
            # transition_invoice's validate_transition should never 409 here.
            # If a concurrent status change ever slipped one through, honour the
            # webhook contract anyway — a 409 must NOT escape and break the
            # silent-204 ack (which would also enumerate invoice state).
            await db.rollback()
            return
        except Exception:
            await db.rollback()
            return  # avoid leaking diagnostic detail in 500 body


async def _raise_erp_reconciliation_exception(
    db: AsyncSession,
    invoice: Invoice,
    *,
    org_id,
    erp_type: str,
    erp_status: str,
    erp_document_id: str | None,
    event_id,
) -> None:
    """Open an ``erp_reconciliation`` Exception for human review.

    Called when the ERP reports an invoice VOIDED/CANCELLED (``→ failed``) that
    we've already advanced past the point where ``→ failed`` is a legal
    transition (``sent_to_erp`` / ``posted_in_erp`` / ``payment_scheduled`` /
    ``paid``). Money may already be in flight, so this is a review signal — we
    deliberately do NOT auto-transition the invoice.

    **Idempotent.** The webhook already dedupes redeliveries by event id, but
    two DISTINCT ERP void events for the same invoice must not pile up duplicate
    reconciliation exceptions — so we skip if an OPEN ``erp_reconciliation``
    exception already exists for this invoice.

    **PII-free.** The ``description`` carries only the safe ERP routing
    identifiers already whitelisted for the audit row (``erp_type`` /
    ``erp_status`` / ``erp_document_id`` / the event id) plus the invoice's
    current status — never the raw ERP ``details`` payload (which may include
    vendor bank / tax / address fields).
    """
    existing = await db.execute(
        select(func.count()).where(
            APException.invoice_id == invoice.id,
            APException.exception_type == ERP_RECONCILIATION_EXCEPTION_TYPE,
            APException.status == "open",
        )
    )
    if (existing.scalar() or 0) > 0:
        return  # already flagged for this invoice — don't duplicate

    description = (
        f"ERP reported '{erp_status}' (void/cancel) via {erp_type} for an "
        f"invoice already at '{invoice.status.value}' — money may be in flight. "
        f"Needs human reconciliation "
        f"(erp_document_id={erp_document_id or '-'}, event={event_id or '-'})."
    )
    await create_exception(
        db,
        exception_type=ERP_RECONCILIATION_EXCEPTION_TYPE,
        severity="error",
        description=description,
        status="open",
        organization_id=org_id,
        invoice=invoice,
    )
