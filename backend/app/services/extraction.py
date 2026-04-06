"""AI invoice extraction service.

Currently a mock implementation that simulates field extraction.
Replace the extract body with a real AI/OCR provider (OpenAI Vision, AWS Textract, etc.).
"""

import uuid
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice import Invoice, InvoiceExtractionResult, InvoiceStatus
from app.services.workflow_engine import (
    get_workflow_instance,
    advance_workflow,
    transition_invoice,
)


async def run_extraction(
    db: AsyncSession,
    invoice: Invoice,
    *,
    actor_id: uuid.UUID | None = None,
) -> None:
    """Extract invoice fields from the uploaded file and update the invoice.

    This is called as a background task after file upload.
    In a production system, this would call an external AI/OCR API.
    """
    try:
        extracted = await _mock_extract(invoice)

        # Update invoice with extracted fields
        invoice.invoice_number = extracted["invoice_number"]
        invoice.vendor_name = extracted["vendor_name"]
        invoice.amount = extracted["amount"]
        invoice.invoice_date = extracted.get("invoice_date")
        invoice.due_date = extracted.get("due_date")
        invoice.subtotal = extracted.get("subtotal")
        invoice.tax_amount = extracted.get("tax_amount")
        invoice.payment_terms = extracted.get("payment_terms")
        invoice.vendor_address = extracted.get("vendor_address")
        invoice.vendor_tax_id = extracted.get("vendor_tax_id")
        invoice.tax_rate = extracted.get("tax_rate")
        invoice.payment_method = extracted.get("payment_method")
        invoice.reference_number = extracted.get("reference_number")
        invoice.description = extracted.get("description", "")

        # Match vendor and link to invoice
        from app.services.vendor_matching import match_and_link_vendor
        vendor, vendor_action = await match_and_link_vendor(
            db, invoice, invoice.organization_id,
        )

        # Save extraction result
        extraction_result = InvoiceExtractionResult(
            invoice_id=invoice.id,
            method="mock",
            confidence=Decimal("0.9500"),
            raw_result=extracted,
        )
        db.add(extraction_result)

        # Transition pending → ready_for_review
        await transition_invoice(
            db,
            invoice,
            InvoiceStatus.ready_for_review,
            actor_id=actor_id,
            action_name="invoice.extraction_completed",
            details={
                "method": "mock",
                "confidence": 0.95,
                "vendor_action": vendor_action,
                "vendor_id": str(vendor.id) if vendor else None,
            },
        )

        # Advance workflow to review step
        instance = await get_workflow_instance(db, invoice.id)
        if instance:
            await advance_workflow(
                db, instance, "review", action="extracted"
            )

        await db.commit()

    except Exception as exc:
        await db.rollback()

        # Transition pending → failed
        await transition_invoice(
            db,
            invoice,
            InvoiceStatus.failed,
            actor_id=actor_id,
            action_name="invoice.extraction_failed",
            details={"error": str(exc)},
        )

        instance = await get_workflow_instance(db, invoice.id)
        if instance:
            instance.state = "failed"
            instance.state_data = {**(instance.state_data or {}), "error": str(exc)}

        await db.commit()


async def _mock_extract(invoice: Invoice) -> dict:
    """Simulate AI extraction — returns plausible invoice data."""
    today = date.today()
    return {
        "invoice_number": f"EXT-{uuid.uuid4().hex[:8].upper()}",
        "vendor_name": "Extracted Vendor Inc",
        "amount": "1500.00",
        "subtotal": "1350.00",
        "tax_amount": "150.00",
        "invoice_date": today.isoformat(),
        "due_date": (today + timedelta(days=30)).isoformat(),
        "payment_terms": "Net 30",
        "vendor_address": "123 Vendor St, Suite 200, Austin, TX 78701",
        "vendor_tax_id": "12-3456789",
        "tax_rate": "10.00",
        "payment_method": "ach",
        "reference_number": f"REF-{uuid.uuid4().hex[:6].upper()}",
        "description": f"Extracted from file: {invoice.file_key or 'unknown'}",
    }
