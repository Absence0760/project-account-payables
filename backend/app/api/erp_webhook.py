"""ERP webhook endpoint — receives status callbacks from ERPs and Merge.dev."""

import uuid

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database import control_session_factory, get_tenant_engine
from app.models.invoice import Invoice, InvoiceStatus
from app.models.organization import Organization
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


@router.post("/webhook/{erp_type}")
async def erp_webhook(
    erp_type: str,
    request: Request,
):
    """Receive status updates from ERPs or Merge.dev.

    Expected body:
    {
        "tenant_slug": "acme",
        "correlation_id": "uuid" | null,
        "erp_document_id": "string" | null,
        "status": "Open" | "posted" | "paid" | ...,
        "details": { ... }  // optional extra data
    }

    For Merge.dev webhooks, the body structure may differ — we normalize it.
    """
    body = await request.json()

    # Normalize — Merge.dev sends a different shape
    if erp_type == "merge_dev" and "data" in body:
        data = body["data"]
        body = {
            "tenant_slug": body.get("linked_account_id"),
            "correlation_id": data.get("integration_params", {}).get("correlation_id"),
            "erp_document_id": data.get("id"),
            "status": data.get("status"),
            "details": data,
        }

    tenant_slug = body.get("tenant_slug")
    correlation_id = body.get("correlation_id")
    erp_document_id = body.get("erp_document_id")
    erp_status = body.get("status", "")
    details = body.get("details")

    if not tenant_slug:
        raise HTTPException(status_code=400, detail="Missing tenant_slug")
    if not correlation_id and not erp_document_id:
        raise HTTPException(status_code=400, detail="Need correlation_id or erp_document_id")

    # Look up the org
    async with control_session_factory() as ctrl_db:
        result = await ctrl_db.execute(select(Organization).where(Organization.slug == tenant_slug))
        org = result.scalar_one_or_none()
        if not org:
            raise HTTPException(status_code=404, detail="Unknown tenant")

    # Map ERP status to our internal status
    target_status = ERP_STATUS_MAP.get(erp_status)
    if not target_status:
        # Unknown status — log but don't transition
        return {"received": True, "action": "ignored", "reason": f"Unknown status: {erp_status}"}

    # Open tenant DB and find the invoice
    engine = get_tenant_engine(org.db_name)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as db:
        try:
            query = select(Invoice)
            if correlation_id:
                query = query.where(Invoice.correlation_id == uuid.UUID(correlation_id))
            else:
                # Fallback: search by ERP doc ID in workflow state_data
                raise HTTPException(status_code=400, detail="correlation_id required for lookup")

            result = await db.execute(query)
            invoice = result.scalar_one_or_none()

            if not invoice:
                raise HTTPException(status_code=404, detail="Invoice not found")

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
                return {
                    "received": True,
                    "action": "skipped",
                    "reason": f"Cannot transition from {current.value} to {target_status.value}",
                }

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

            return {
                "received": True,
                "action": "transitioned",
                "from": current.value,
                "to": target_status.value,
            }

        except HTTPException:
            raise
        except Exception as exc:
            await db.rollback()
            raise HTTPException(status_code=500, detail=str(exc))
