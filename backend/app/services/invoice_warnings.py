"""Compute warnings and fraud flags for invoices."""

from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice import Invoice


async def compute_warnings(db: AsyncSession, invoices: list[Invoice]) -> dict[str, list[dict]]:
    """Return a dict of invoice_id → list of warning dicts."""
    if not invoices:
        return {}

    # Pre-fetch duplicate candidates: same vendor + invoice_number with count > 1
    dup_query = (
        select(
            Invoice.vendor_name,
            Invoice.invoice_number,
            func.count().label("cnt"),
        )
        .where(Invoice.invoice_number != "", Invoice.vendor_name != "")
        .group_by(Invoice.vendor_name, Invoice.invoice_number)
        .having(func.count() > 1)
    )
    dup_result = await db.execute(dup_query)
    dup_pairs = {(r.vendor_name, r.invoice_number) for r in dup_result}

    result: dict[str, list[dict]] = {}

    for inv in invoices:
        warnings: list[dict] = []
        inv_id = str(inv.id)

        # Missing required fields
        if not inv.vendor_name or not inv.vendor_name.strip():
            warnings.append({"type": "missing_field", "severity": "error", "message": "Missing vendor name"})
        if not inv.invoice_number or not inv.invoice_number.strip():
            warnings.append({"type": "missing_field", "severity": "error", "message": "Missing invoice number"})
        if inv.amount is None or inv.amount <= 0:
            warnings.append({"type": "missing_field", "severity": "error", "message": "Missing or zero amount"})

        # Duplicate detection
        if inv.vendor_name and inv.invoice_number and (inv.vendor_name, inv.invoice_number) in dup_pairs:
            warnings.append({"type": "duplicate", "severity": "warning", "message": "Duplicate invoice number for this vendor"})

        # Fraud flags
        if inv.amount and inv.amount > 0:
            # Round amount (exact thousands)
            if inv.amount >= 1000 and inv.amount % 1000 == 0:
                warnings.append({"type": "fraud_round_amount", "severity": "info", "message": f"Round amount: {inv.amount}"})

        # Future invoice date
        if inv.invoice_date and inv.invoice_date > date.today():
            warnings.append({"type": "fraud_future_date", "severity": "warning", "message": "Invoice date is in the future"})

        # Past-due
        if inv.due_date and inv.due_date < date.today() and inv.status.value in ("new", "pending", "ready_for_review"):
            warnings.append({"type": "past_due", "severity": "warning", "message": "Invoice is past due"})

        if warnings:
            result[inv_id] = warnings

    return result
