"""PO matching service — match invoices against purchase orders and goods receipts.

Supports 2-way (invoice vs PO) and 3-way (invoice vs PO vs GR) matching.
"""

import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.invoice import Invoice
from app.models.procurement import PurchaseOrder, GoodsReceipt


@dataclass
class MatchResult:
    """Result of PO matching for an invoice."""

    match_type: str = "none"  # "none", "2-way", "3-way"
    status: str = "no_po"  # "no_po", "matched", "mismatch", "partial"

    po_id: str | None = None
    po_number: str | None = None
    po_total: float | None = None
    gr_id: str | None = None

    amount_variance: float = 0.0  # invoice - PO
    amount_variance_pct: float = 0.0
    within_tolerance: bool = False

    issues: list[str] = field(default_factory=list)
    details: dict = field(default_factory=dict)


async def match_invoice_to_po(
    db: AsyncSession,
    invoice: Invoice,
    tolerance_pct: float = 5.0,
) -> MatchResult:
    """Match an invoice against POs and goods receipts.

    1. Find PO by po_number on the invoice
    2. Compare amounts (2-way match)
    3. If GR exists for the PO, verify quantities (3-way match)

    Args:
        tolerance_pct: Allowed variance percentage (default 5%)

    Returns:
        MatchResult with match status, variances, and issues
    """
    result = MatchResult()

    if not invoice.po_number:
        result.status = "no_po"
        return result

    # Find PO by number
    po_query = select(PurchaseOrder).where(
        PurchaseOrder.po_number == invoice.po_number,
    ).options(selectinload(PurchaseOrder.line_items))

    # If invoice has a vendor_id, also match on vendor
    if invoice.vendor_id:
        po_query = po_query.where(PurchaseOrder.vendor_id == invoice.vendor_id)

    po_result = await db.execute(po_query)
    po = po_result.scalar_one_or_none()

    if not po:
        result.status = "no_po"
        result.issues.append(f"PO {invoice.po_number} not found")
        return result

    result.po_id = str(po.id)
    result.po_number = po.po_number
    result.po_total = float(po.total)

    # 2-way match: invoice amount vs PO total
    invoice_amount = float(invoice.amount)
    po_total = float(po.total)

    result.amount_variance = invoice_amount - po_total
    if po_total > 0:
        result.amount_variance_pct = (result.amount_variance / po_total) * 100
    else:
        result.amount_variance_pct = 100.0 if invoice_amount > 0 else 0.0

    result.within_tolerance = abs(result.amount_variance_pct) <= tolerance_pct
    result.match_type = "2-way"

    if not result.within_tolerance:
        result.status = "mismatch"
        result.issues.append(
            f"Amount mismatch: invoice ${invoice_amount:.2f} vs PO ${po_total:.2f} "
            f"({result.amount_variance_pct:+.1f}%)"
        )
    else:
        result.status = "matched"

    # 3-way match: check for goods receipt
    gr_result = await db.execute(
        select(GoodsReceipt)
        .where(GoodsReceipt.po_id == po.id)
        .options(selectinload(GoodsReceipt.line_items))
    )
    gr = gr_result.scalar_one_or_none()

    if gr:
        result.match_type = "3-way"
        result.gr_id = str(gr.id)

        # Compare line quantities if both have line items
        if po.line_items and gr.line_items:
            po_qty_total = sum(
                float(li.quantity or 0) for li in po.line_items
            )
            gr_qty_total = sum(
                float(li.quantity_received or 0) for li in gr.line_items
            )

            if po_qty_total > 0 and gr_qty_total < po_qty_total:
                pct_received = (gr_qty_total / po_qty_total) * 100
                result.issues.append(
                    f"Partial receipt: {pct_received:.0f}% of ordered quantity received"
                )
                if result.status == "matched":
                    result.status = "partial"

    result.details = {
        "match_type": result.match_type,
        "po_total": result.po_total,
        "invoice_amount": invoice_amount,
        "variance": result.amount_variance,
        "variance_pct": result.amount_variance_pct,
        "tolerance_pct": tolerance_pct,
        "within_tolerance": result.within_tolerance,
        "has_gr": gr is not None,
    }

    return result
