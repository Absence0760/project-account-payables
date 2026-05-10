"""Compute warnings and fraud flags for invoices — persisted on write.

Also creates exception records for issues that need human resolution.
"""

from dataclasses import asdict
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.exception import Exception as APException
from app.models.invoice import Invoice
from app.services.po_matching import match_invoice_to_po


def _status_str(status) -> str:
    """Normalize an invoice status into a plain string.

    Handles three shapes that reach us in practice:
      - `InvoiceStatus` (StrEnum on the SQLA model) — `.value` works
        but `str(...)` is also correct since StrEnum inherits from str.
      - Plain string, e.g. after `setattr(invoice, "status", "x")` in
        `update_invoice` — has no `.value` attribute. This was a real
        500 in production until this helper landed.
      - Test-fixture mocks like `SimpleNamespace(value=...)` — no
        `__str__` override; `getattr(..., 'value', ...)` covers them.
    """
    return getattr(status, "value", status) if not isinstance(status, str) else status


async def refresh_warnings(db: AsyncSession, invoice: Invoice) -> list[dict]:
    """Recompute warnings for a single invoice and persist them on the row.

    Also creates exception records for actionable issues.
    """
    warnings: list[dict] = []

    # Missing required fields
    if not invoice.vendor_name or not invoice.vendor_name.strip():
        warnings.append(
            {"type": "missing_field", "severity": "error", "message": "Missing vendor name"}
        )
    if not invoice.invoice_number or not invoice.invoice_number.strip():
        warnings.append(
            {"type": "missing_field", "severity": "error", "message": "Missing invoice number"}
        )
    if invoice.amount is None or invoice.amount <= 0:
        warnings.append(
            {"type": "missing_field", "severity": "error", "message": "Missing or zero amount"}
        )

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
            warnings.append(
                {
                    "type": "duplicate",
                    "severity": "warning",
                    "message": "Duplicate invoice number for this vendor",
                }
            )
            await _ensure_exception(
                db, invoice, "duplicate", "warning", "Duplicate invoice number for this vendor"
            )

    # Fraud flags
    if invoice.amount and invoice.amount > 0:
        if invoice.amount >= 1000 and invoice.amount % 1000 == 0:
            warnings.append(
                {
                    "type": "fraud_round_amount",
                    "severity": "info",
                    "message": f"Round amount: {invoice.amount}",
                }
            )
            await _ensure_exception(
                db, invoice, "fraud_flag", "info", f"Suspicious round amount: ${invoice.amount}"
            )

    if invoice.invoice_date and invoice.invoice_date > date.today():
        warnings.append(
            {
                "type": "fraud_future_date",
                "severity": "warning",
                "message": "Invoice date is in the future",
            }
        )
        await _ensure_exception(
            db, invoice, "fraud_flag", "warning", "Invoice date is in the future"
        )

    if (
        invoice.due_date
        and invoice.due_date < date.today()
        and _status_str(invoice.status) in ("new", "pending", "ready_for_review")
    ):
        warnings.append(
            {"type": "past_due", "severity": "warning", "message": "Invoice is past due"}
        )

    # Unverified vendor
    if invoice.vendor_id:
        from app.models.vendor import Vendor

        v_result = await db.execute(select(Vendor.status).where(Vendor.id == invoice.vendor_id))
        vendor_status = v_result.scalar_one_or_none()
        if vendor_status == "unverified":
            warnings.append(
                {
                    "type": "unverified_vendor",
                    "severity": "warning",
                    "message": "Vendor is unverified",
                }
            )
            await _ensure_exception(
                db,
                invoice,
                "unverified_vendor",
                "warning",
                "Invoice linked to an unverified vendor",
            )

    # Missing data (no amount after extraction)
    has_missing = any(w["type"] == "missing_field" for w in warnings)
    if has_missing and _status_str(invoice.status) not in ("new",):
        await _ensure_exception(
            db, invoice, "missing_data", "error", "Required fields missing after extraction"
        )

    # PO matching — runs whenever the invoice has a po_number. The match
    # service handles 2-way (invoice vs PO) and 3-way (with goods receipt).
    # Result is persisted on `invoice.po_match` so the modal can render it
    # without re-running. Mismatches and missing POs raise exceptions for
    # the queue. Skip on draft `new` invoices that haven't been extracted yet.
    if invoice.po_number and _status_str(invoice.status) != "new":
        await _refresh_po_match(db, invoice, warnings)
    else:
        invoice.po_match = None

    # Persist
    invoice.warnings = warnings or None
    return warnings


async def _refresh_po_match(db: AsyncSession, invoice: Invoice, warnings: list[dict]) -> None:
    """Run PO matching and append to warnings + exceptions on issues.

    Stores the structured result on `invoice.po_match` for UI rendering.
    Mutates `warnings` in place.
    """
    match = await match_invoice_to_po(db, invoice)
    invoice.po_match = asdict(match)

    if match.status == "no_po":
        warnings.append(
            {
                "type": "po_mismatch",
                "severity": "error",
                "message": f"PO {invoice.po_number} not found",
            }
        )
        await _ensure_exception(
            db,
            invoice,
            "po_mismatch",
            "error",
            f"Invoice references PO {invoice.po_number} but no matching PO exists",
        )
    elif match.status == "mismatch":
        # Lead with the most useful number — variance %.
        msg = (
            f"Amount variance {match.amount_variance_pct:+.1f}% vs PO {match.po_number} "
            f"(invoice ${invoice.amount} vs PO ${match.po_total:.2f})"
        )
        warnings.append({"type": "po_mismatch", "severity": "warning", "message": msg})
        await _ensure_exception(db, invoice, "po_mismatch", "warning", msg)
    elif match.status == "partial":
        # Partial 3-way receipt — informational. Reviewer needs to know but
        # it's not an error; goods may be in transit.
        msg = (
            f"Partial 3-way match — {match.match_type} match against PO {match.po_number}, "
            f"but only part of the ordered quantity has been received"
        )
        warnings.append({"type": "po_mismatch", "severity": "info", "message": msg})
        await _ensure_exception(db, invoice, "po_mismatch", "info", msg)


async def _ensure_exception(
    db: AsyncSession,
    invoice: Invoice,
    exception_type: str,
    severity: str,
    description: str,
) -> None:
    """Create an exception if one doesn't already exist for this invoice + type."""
    existing = await db.execute(
        select(func.count()).where(
            APException.invoice_id == invoice.id,
            APException.exception_type == exception_type,
            APException.status.in_(["open", "escalated"]),
        )
    )
    if (existing.scalar() or 0) > 0:
        return  # already exists

    db.add(
        APException(
            invoice_id=invoice.id,
            exception_type=exception_type,
            severity=severity,
            description=description,
            status="open",
            organization_id=invoice.organization_id,
        )
    )
