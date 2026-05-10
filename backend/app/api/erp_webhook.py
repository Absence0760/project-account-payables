"""ERP webhook endpoint — receives status callbacks from ERPs and Merge.dev."""

import uuid

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database import control_session_factory, get_tenant_engine
from app.models.invoice import Invoice, InvoiceStatus
from app.models.organization import Organization
from app.services.webhook_security import (
    extract_signature_header,
    is_event_already_processed,
    verify_hmac_sha256,
)
from app.services.workflow_engine import transition_invoice

router = APIRouter(prefix="/erp", tags=["erp"])


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
    event_id = body.get("event_id") or erp_document_id or correlation_id
    details = body.get("details")

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

            # Only transition if the target is a valid forward step
            current = invoice.status
            valid_transitions = {
                InvoiceStatus.sent_to_erp: {InvoiceStatus.posted_in_erp, InvoiceStatus.failed},
                InvoiceStatus.posted_in_erp: {
                    InvoiceStatus.payment_scheduled,
                    InvoiceStatus.paid,
                    InvoiceStatus.failed,
                },
                InvoiceStatus.payment_scheduled: {InvoiceStatus.paid, InvoiceStatus.failed},
            }

            allowed = valid_transitions.get(current, set())
            if target_status not in allowed:
                return  # not a legal forward step — silent ack

            await transition_invoice(
                db,
                invoice,
                target_status,
                action_name=f"invoice.erp_status_{target_status.value}",
                details={
                    "erp_type": erp_type,
                    "erp_status": erp_status,
                    "erp_document_id": erp_document_id,
                    **(details or {}),
                },
            )
            await db.commit()
            return

        except HTTPException:
            raise
        except Exception:
            await db.rollback()
            return  # avoid leaking diagnostic detail in 500 body
