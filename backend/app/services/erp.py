"""ERP integration service — push approved invoices to an external ERP system."""

import asyncio
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice import Invoice, InvoiceStatus
from app.services.erp_adapters import (
    InvoicePayload,
    get_erp_adapter,
)
from app.services.workflow_engine import (
    complete_workflow,
    get_workflow_instance,
    transition_invoice,
)

MAX_RETRIES = 3
BASE_DELAY_SECONDS = 2


def _build_payload(invoice: Invoice) -> InvoicePayload:
    """Convert an Invoice ORM object to a normalized ERP payload."""
    return InvoicePayload(
        correlation_id=str(invoice.correlation_id),
        invoice_number=invoice.invoice_number,
        vendor_name=invoice.vendor_name,
        amount=invoice.amount,
        currency=invoice.currency or "USD",
        vendor_tax_id=invoice.vendor_tax_id,
        invoice_date=invoice.invoice_date,
        due_date=invoice.due_date,
        po_number=invoice.po_number,
        description=invoice.description,
        subtotal=invoice.subtotal,
        tax_amount=invoice.tax_amount,
        tax_rate=invoice.tax_rate,
        discount_amount=invoice.discount_amount,
        shipping_amount=invoice.shipping_amount,
        gl_account=invoice.gl_account,
        cost_center=invoice.cost_center,
        payment_terms=invoice.payment_terms,
        payment_method=invoice.payment_method,
        bill_to_address=invoice.bill_to_address,
        remit_to_address=invoice.remit_to_address,
        vendor_address=invoice.vendor_address,
    )


async def send_to_erp(
    db: AsyncSession,
    invoice: Invoice,
    *,
    actor_id: uuid.UUID | None = None,
    erp_config: dict | None = None,
) -> None:
    """Initiate ERP submission. Handles retries with exponential backoff."""
    # Transition approved → sending_to_erp
    await transition_invoice(
        db,
        invoice,
        InvoiceStatus.sending_to_erp,
        actor_id=actor_id,
        action_name="invoice.erp_submitted",
    )
    await db.commit()

    # Attempt the ERP call with retries
    instance = await get_workflow_instance(db, invoice.id)
    state_data = (instance.state_data if instance else None) or {}
    retry_count = state_data.get("erp_retries", 0)

    for attempt in range(retry_count, MAX_RETRIES):
        try:
            erp_ref = await _call_erp(invoice, erp_config)

            # Success → sent_to_erp → done
            await transition_invoice(
                db,
                invoice,
                InvoiceStatus.sent_to_erp,
                actor_id=actor_id,
                action_name="invoice.erp_confirmed",
                details={"erp_reference": erp_ref},
            )
            await transition_invoice(
                db,
                invoice,
                InvoiceStatus.done,
                actor_id=actor_id,
                action_name="invoice.completed",
            )

            if instance:
                instance.state_data = {
                    **(instance.state_data or {}),
                    "erp_reference": erp_ref,
                    "erp_retries": attempt + 1,
                }
                await complete_workflow(db, instance, action="erp_confirmed")

            await db.commit()
            return

        except Exception as exc:
            if attempt + 1 < MAX_RETRIES:
                # Update retry count and wait before next attempt
                if instance:
                    instance.state_data = {
                        **(instance.state_data or {}),
                        "erp_retries": attempt + 1,
                        "last_error": str(exc),
                    }
                    await db.commit()
                delay = BASE_DELAY_SECONDS * (2**attempt)
                await asyncio.sleep(delay)
            else:
                # All retries exhausted → failed
                await transition_invoice(
                    db,
                    invoice,
                    InvoiceStatus.failed,
                    actor_id=actor_id,
                    action_name="invoice.erp_failed",
                    details={"error": str(exc), "retries": attempt + 1},
                )
                if instance:
                    instance.state = "failed"
                    instance.state_data = {
                        **(instance.state_data or {}),
                        "erp_retries": attempt + 1,
                        "last_error": str(exc),
                    }
                await db.commit()
                return


async def retry_erp(
    db: AsyncSession,
    invoice: Invoice,
    *,
    actor_id: uuid.UUID | None = None,
) -> None:
    """Retry a failed ERP push. Only valid if the invoice was previously approved."""
    if not invoice.approved_by:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=409,
            detail="Cannot retry ERP push — invoice was never approved",
        )

    # Reset retry count
    instance = await get_workflow_instance(db, invoice.id)
    if instance:
        state_data = instance.state_data or {}
        state_data["erp_retries"] = 0
        instance.state_data = state_data
        instance.state = "active"

    await transition_invoice(
        db,
        invoice,
        InvoiceStatus.sending_to_erp,
        actor_id=actor_id,
        action_name="invoice.erp_retried",
    )
    await db.commit()

    # Re-run the ERP call
    await send_to_erp_internal(db, invoice, actor_id=actor_id)


async def send_to_erp_internal(
    db: AsyncSession,
    invoice: Invoice,
    *,
    actor_id: uuid.UUID | None = None,
    erp_config: dict | None = None,
) -> None:
    """Run ERP call (invoice is already in sending_to_erp state)."""
    instance = await get_workflow_instance(db, invoice.id)
    state_data = (instance.state_data if instance else None) or {}

    try:
        erp_ref = await _call_erp(invoice, erp_config)

        await transition_invoice(
            db,
            invoice,
            InvoiceStatus.sent_to_erp,
            actor_id=actor_id,
            action_name="invoice.erp_confirmed",
            details={"erp_reference": erp_ref},
        )
        await transition_invoice(
            db,
            invoice,
            InvoiceStatus.done,
            actor_id=actor_id,
            action_name="invoice.completed",
        )

        if instance:
            instance.state_data = {**state_data, "erp_reference": erp_ref}
            await complete_workflow(db, instance, action="erp_confirmed")

        await db.commit()

    except Exception as exc:
        await transition_invoice(
            db,
            invoice,
            InvoiceStatus.failed,
            actor_id=actor_id,
            action_name="invoice.erp_failed",
            details={"error": str(exc)},
        )
        if instance:
            instance.state = "failed"
            instance.state_data = {**state_data, "last_error": str(exc)}
        await db.commit()


async def _call_erp(invoice: Invoice, erp_config: dict | None = None) -> str:
    """Send invoice to the configured ERP via the adapter pattern.

    Uses the invoice's correlation_id as an idempotency key.
    Returns an ERP reference ID on success, raises on failure.
    """
    config = erp_config or {"type": "mock", "integration_method": "direct"}

    # Import adapters to trigger registration
    import app.services.erp_adapters.dynamics_365_bc  # noqa: F401
    import app.services.erp_adapters.merge_dev  # noqa: F401
    import app.services.erp_adapters.mock_adapter  # noqa: F401
    import app.services.erp_adapters.netsuite  # noqa: F401

    adapter = get_erp_adapter(config)
    payload = _build_payload(invoice)
    result = await adapter.post_invoice(payload)

    if not result.success:
        raise RuntimeError(result.message or "ERP post failed")

    return result.erp_document_id or result.erp_document_number or "UNKNOWN"
