"""Compute warnings and fraud flags for invoices — persisted on write."""

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice import Invoice


async def refresh_warnings(db: AsyncSession, invoice: Invoice) -> list[dict]:
    """Recompute warnings for a single invoice and persist them on the row."""
    warnings: list[dict] = []

    # Missing required fields
    if not invoice.vendor_name or not invoice.vendor_name.strip():
        warnings.append({"type": "missing_field", "severity": "error", "message": "Missing vendor name"})
    if not invoice.invoice_number or not invoice.invoice_number.strip():
        warnings.append({"type": "missing_field", "severity": "error", "message": "Missing invoice number"})
    if invoice.amount is None or invoice.amount <= 0:
        warnings.append({"type": "missing_field", "severity": "error", "message": "Missing or zero amount"})

    # Duplicate detection — check if another invoice has the same vendor + invoice #
    if invoice.vendor_name and invoice.invoice_number:
        dup_count = await db.execute(
            select(func.count())
            .select_from(Invoice)
            .where(
                Invoice.vendor_name == invoice.vendor_name,
                Invoice.invoice_number == invoice.invoice_number,
                Invoice.id != invoice.id,
            )
        )
        if (dup_count.scalar() or 0) > 0:
            warnings.append({"type": "duplicate", "severity": "warning", "message": "Duplicate invoice number for this vendor"})

    # Fraud flags
    if invoice.amount and invoice.amount > 0:
        if invoice.amount >= 1000 and invoice.amount % 1000 == 0:
            warnings.append({"type": "fraud_round_amount", "severity": "info", "message": f"Round amount: {invoice.amount}"})

    if invoice.invoice_date and invoice.invoice_date > date.today():
        warnings.append({"type": "fraud_future_date", "severity": "warning", "message": "Invoice date is in the future"})

    if invoice.due_date and invoice.due_date < date.today() and invoice.status.value in ("new", "pending", "ready_for_review"):
        warnings.append({"type": "past_due", "severity": "warning", "message": "Invoice is past due"})

    # Persist
    invoice.warnings = warnings or None
    return warnings
