"""PO matching service — match invoices against purchase orders and goods receipts.

Supports 2-way (invoice vs PO), 3-way (invoice vs PO vs GR), and 4-way
(invoice vs PO vs GR vs Quality Inspection) matching.
"""

from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.invoice import Invoice
from app.models.procurement import GoodsReceipt, PurchaseOrder
from app.models.quality_inspection import QualityInspection


def _to_decimal(value, default: Decimal = Decimal("0")) -> Decimal:
    """Coerce a money/percent value to exact Decimal via str() (never Decimal(float)).

    Floats are bridged through ``str`` so a config literal like ``5.1`` lands as
    ``Decimal('5.1')`` rather than the binary-float artefact. Unparseable /
    ``None`` values fall back to ``default``."""
    if value is None:
        return default
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return default


def _json_safe(value):
    """Recursively convert Decimals to JSON-serialisable floats.

    ``MatchResult`` carries exact Decimal money fields in memory (money is never
    float), but ``invoice.po_match`` is a JSONB column whose default serialiser
    can't encode Decimal. This renders the persisted display artefact back to the
    numeric wire shape every downstream reader (UI modal, analytics) expects,
    while every arithmetic/comparison upstream stayed in exact Decimal."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


@dataclass
class MatchResult:
    """Result of PO matching for an invoice.

    Money fields (``po_total``, ``amount_variance``, ``amount_variance_pct``) are
    exact ``Decimal`` — the tolerance gate and every variance figure are computed
    and compared in Decimal, never float. Use ``to_json_dict()`` to persist onto
    the ``invoice.po_match`` JSONB column (Decimals rendered back to numbers)."""

    match_type: str = "none"  # "none", "2-way", "3-way", "4-way"
    status: str = "no_po"  # "no_po", "matched", "mismatch", "partial"

    po_id: str | None = None
    po_number: str | None = None
    po_total: Decimal | None = None
    gr_id: str | None = None

    amount_variance: Decimal = Decimal("0")  # invoice - PO
    amount_variance_pct: Decimal = Decimal("0")
    within_tolerance: bool = False

    # 4-way: quality inspection leg
    inspection_id: str | None = None
    inspection_result: str | None = None  # 'pass' | 'fail' | 'partial' or None
    inspection_accepted_quantity: float | None = None
    inspection_required: bool = False

    issues: list[str] = field(default_factory=list)
    details: dict = field(default_factory=dict)

    def to_json_dict(self) -> dict:
        """JSON-safe dict for the ``invoice.po_match`` JSONB column."""
        return _json_safe(asdict(self))


async def match_invoice_to_po(
    db: AsyncSession,
    invoice: Invoice,
    tolerance_pct: Decimal | float | int | str = Decimal("5.0"),
    require_inspection: bool = False,
) -> MatchResult:
    """Match an invoice against POs, goods receipts, and quality inspections.

    1. Find PO by po_number on the invoice
    2. Compare amounts (2-way match)
    3. If GR exists for the PO, verify quantities (3-way match)
    4. If a quality inspection exists, fold its verdict in (4-way match)

    Args:
        tolerance_pct: Allowed variance percentage (default 5%)
        require_inspection: When True, a PO match with no inspection record is
            flagged (inspection_required) so the warnings layer can raise a
            quality_hold.

    Returns:
        MatchResult with match status, variances, and issues
    """
    result = MatchResult()

    if not invoice.po_number:
        result.status = "no_po"
        return result

    # Find PO by number
    po_query = (
        select(PurchaseOrder)
        .where(
            PurchaseOrder.po_number == invoice.po_number,
        )
        .options(selectinload(PurchaseOrder.line_items))
    )

    # If invoice has a vendor_id, also match on vendor
    if invoice.vendor_id:
        po_query = po_query.where(PurchaseOrder.vendor_id == invoice.vendor_id)

    # A po_number is not unique (it may be re-used across entities, or shared by
    # two vendors when the invoice carries no vendor_id to disambiguate), so cap
    # the lookup at a single deterministic row — newest first — rather than
    # `scalar_one_or_none()`, which raises MultipleResultsFound on >1 PO and
    # takes down the whole matcher / refresh_warnings pipeline.
    po_query = po_query.order_by(PurchaseOrder.created_at.desc()).limit(1)

    po_result = await db.execute(po_query)
    po = po_result.scalar_one_or_none()

    if not po:
        result.status = "no_po"
        result.issues.append(f"PO {invoice.po_number} not found")
        return result

    result.po_id = str(po.id)
    result.po_number = po.po_number
    result.po_total = _to_decimal(po.total)

    # 2-way match: invoice amount vs PO total. Every figure — the variance, the
    # variance %, and the tolerance gate — is exact Decimal end-to-end; money is
    # never cast to float. On a boundary amount (e.g. exactly 5.01% over a PO) an
    # IEEE-754 residual could otherwise flip `<= tolerance_pct` and auto-match an
    # out-of-tolerance invoice (or falsely flag a clean one). The result fields
    # stay Decimal in memory; `to_json_dict()` renders them for the JSONB column.
    invoice_amount = _to_decimal(invoice.amount)
    po_total = _to_decimal(po.total)
    tolerance = _to_decimal(tolerance_pct, Decimal("5.0"))

    variance = invoice_amount - po_total
    if po_total > 0:
        variance_pct = (variance / po_total) * Decimal(100)
    else:
        variance_pct = Decimal(100) if invoice_amount > 0 else Decimal(0)

    result.amount_variance = variance
    result.amount_variance_pct = variance_pct
    result.within_tolerance = abs(variance_pct) <= tolerance
    result.match_type = "2-way"

    if not result.within_tolerance:
        result.status = "mismatch"
        result.issues.append(
            f"Amount mismatch: invoice ${invoice_amount:.2f} vs PO ${po_total:.2f} "
            f"({variance_pct:+.1f}%)"
        )
    else:
        result.status = "matched"

    # 3-way match: check for goods receipts. A PO can have SEVERAL goods receipts
    # (the normal partial-delivery case — a PO filled by several shipments, each
    # a separate GR). Fetch them ALL (newest first) rather than a single row via
    # `scalar_one_or_none()`, which raised MultipleResultsFound on >1 GR and
    # crashed the matcher. The received-quantity comparison sums across every GR
    # (so a PO fully filled by two shipments is `matched`, not falsely `partial`);
    # the newest GR is the representative row for `gr_id` + the inspection leg.
    grs = (
        (
            await db.execute(
                select(GoodsReceipt)
                .where(GoodsReceipt.po_id == po.id)
                .options(selectinload(GoodsReceipt.line_items))
                .order_by(GoodsReceipt.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    gr = grs[0] if grs else None

    if gr:
        result.match_type = "3-way"
        result.gr_id = str(gr.id)

        # Compare received vs ordered quantity, aggregating receipts across ALL
        # GRs for the PO. Only when the PO has line items and at least one GR
        # carries received lines (an empty GR header has nothing to verify).
        if po.line_items and any(g.line_items for g in grs):
            po_qty_total = sum((_to_decimal(li.quantity) for li in po.line_items), Decimal("0"))
            gr_qty_total = sum(
                (_to_decimal(li.quantity_received) for g in grs for li in g.line_items),
                Decimal("0"),
            )

            if po_qty_total > 0 and gr_qty_total < po_qty_total:
                pct_received = (gr_qty_total / po_qty_total) * Decimal(100)
                result.issues.append(
                    f"Partial receipt: {pct_received:.0f}% of ordered quantity received"
                )
                if result.status == "matched":
                    result.status = "partial"

    # 4-way match: check for a quality inspection. Prefer one tied to the
    # goods receipt (the goods we actually received); fall back to one tied
    # to the PO. Take the most recent.
    inspection_query = select(QualityInspection)
    if gr is not None:
        inspection_query = inspection_query.where(QualityInspection.gr_id == gr.id)
    else:
        inspection_query = inspection_query.where(QualityInspection.po_id == po.id)
    inspection_query = inspection_query.order_by(QualityInspection.created_at.desc()).limit(1)

    inspection_result = await db.execute(inspection_query)
    inspection = inspection_result.scalar_one_or_none()

    if inspection is not None:
        result.match_type = "4-way"
        result.inspection_id = str(inspection.id)
        result.inspection_result = inspection.result
        if inspection.accepted_quantity is not None:
            result.inspection_accepted_quantity = float(inspection.accepted_quantity)

        if inspection.result == "fail":
            result.status = "mismatch"
            msg = "Failed quality inspection"
            if inspection.deviation_notes:
                msg = f"{msg}: {inspection.deviation_notes}"
            result.issues.append(msg)
        elif inspection.result == "partial":
            if result.status == "matched":
                result.status = "partial"
            accepted = (
                f"{result.inspection_accepted_quantity:g}"
                if result.inspection_accepted_quantity is not None
                else "part"
            )
            result.issues.append(f"Partial acceptance: {accepted} of ordered quantity accepted")
        # result == "pass" -> no status change
    elif require_inspection and po is not None:
        result.inspection_required = True
        result.issues.append("Quality inspection required but missing")

    # Decimal values here are rendered to numbers by `to_json_dict()` at the
    # JSONB boundary — the arithmetic that produced them was exact Decimal.
    result.details = {
        "match_type": result.match_type,
        "po_total": result.po_total,
        "invoice_amount": invoice_amount,
        "variance": result.amount_variance,
        "variance_pct": result.amount_variance_pct,
        "tolerance_pct": tolerance,
        "within_tolerance": result.within_tolerance,
        "has_gr": gr is not None,
        "has_inspection": inspection is not None,
        "inspection_result": result.inspection_result,
    }

    return result
