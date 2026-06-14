"""Purchase-requisition helpers — kept out of the router so the route handlers
stay thin.

Holds: requisition-number generation, exact line-total + header-total recompute
(all ``Decimal`` math, never float), the status-transition guard (a small state
machine mirroring the expense-report approval shape), and the requisition→PO
conversion (creates a ``PurchaseOrder`` + ``POLineItem`` rows from the
requisition lines). Conversion idempotency is owned by the router (it checks
``converted_po_id`` before calling :func:`convert_requisition_to_po`).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import HTTPException, status

from app.models.procurement import (
    POLineItem,
    PurchaseOrder,
    PurchaseRequisition,
    RequisitionLineItem,
    RequisitionStatus,
)

# Allowed source → target requisition status transitions. An invalid source
# status is a 422 at the route boundary (never a silent no-op). ``converted``
# is driven by the convert-to-PO route, not a free-standing transition.
VALID_TRANSITIONS: dict[RequisitionStatus, set[RequisitionStatus]] = {
    RequisitionStatus.draft: {
        RequisitionStatus.pending_approval,
        RequisitionStatus.cancelled,
    },
    RequisitionStatus.submitted: {
        RequisitionStatus.pending_approval,
        RequisitionStatus.cancelled,
    },
    RequisitionStatus.pending_approval: {
        RequisitionStatus.approved,
        RequisitionStatus.rejected,
        RequisitionStatus.cancelled,
    },
    RequisitionStatus.approved: {
        RequisitionStatus.converted,
        RequisitionStatus.cancelled,
    },
    RequisitionStatus.rejected: {
        RequisitionStatus.draft,
    },
    RequisitionStatus.converted: set(),  # terminal
    RequisitionStatus.cancelled: set(),  # terminal
}


def guard_transition(current: RequisitionStatus, target: RequisitionStatus) -> None:
    """Raise 422 if ``current → target`` is not an allowed requisition move."""
    if target not in VALID_TRANSITIONS.get(current, set()):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Cannot move a requisition from '{current}' to '{target}'.",
        )


def line_total(quantity: Decimal | None, unit_price: Decimal | None) -> Decimal | None:
    """Exact ``quantity * unit_price``. Returns ``None`` when either side is
    absent (a description-only line carries no money)."""
    if quantity is None or unit_price is None:
        return None
    return Decimal(quantity) * Decimal(unit_price)


def recompute_total(req: PurchaseRequisition) -> Decimal:
    """Recompute the requisition header ``total`` from its line items.

    Sums each line's ``total`` (already ``quantity * unit_price``) as ``Decimal``
    so the header total can never drift from its lines. Lines with no money
    (``total is None``) contribute zero."""
    total = Decimal("0")
    for li in req.line_items:
        if li.total is not None:
            total += Decimal(li.total)
    req.total = total
    return total


def build_line_items(rows: list) -> list[RequisitionLineItem]:
    """Build ``RequisitionLineItem`` rows from create/update payload items,
    stamping each line's exact ``total``. ``rows`` are
    ``RequisitionLineItemCreate`` schema instances."""
    out: list[RequisitionLineItem] = []
    for idx, row in enumerate(rows, start=1):
        catalog_item_uuid = _opt_uuid(row.catalog_item_id, "catalog_item_id")
        gl_uuid = _opt_uuid(row.gl_account_id, "gl_account_id")
        out.append(
            RequisitionLineItem(
                line_number=row.line_number if row.line_number is not None else idx,
                catalog_item_id=catalog_item_uuid,
                item_code=row.item_code,
                description=row.description,
                quantity=row.quantity,
                unit_price=row.unit_price,
                total=line_total(row.quantity, row.unit_price),
                gl_account_id=gl_uuid,
                uom=row.uom,
            )
        )
    return out


def next_requisition_number(existing_count: int) -> str:
    """Deterministic-ish requisition number when the client doesn't supply one.

    Format ``REQ-<YYYY>-<seq>`` where ``seq`` is ``existing_count + 1``
    zero-padded. The caller passes the current row count; uniqueness is not
    DB-enforced (the schema has no unique index), so the API requires the client
    to supply ``requisition_number`` — this is only the convenience default."""
    year = datetime.now(UTC).year
    return f"REQ-{year}-{existing_count + 1:05d}"


def convert_requisition_to_po(
    req: PurchaseRequisition,
    *,
    org_id: uuid.UUID,
    po_number: str,
) -> PurchaseOrder:
    """Build a ``PurchaseOrder`` (+ ``POLineItem`` rows) from an approved
    requisition's lines.

    Pure construction — the caller adds the PO to the session, flushes, links
    ``req.converted_po_id`` / ``req.status`` and writes the audit row. The PO
    inherits the requisition's entity, vendor, and exact ``total``; each line's
    ``total`` carries over unchanged (already ``Decimal``)."""
    po = PurchaseOrder(
        po_number=po_number,
        vendor_id=req.vendor_id,
        total=Decimal(req.total or 0),
        status="open",
        organization_id=org_id,
        entity_id=req.entity_id,
    )
    for li in req.line_items:
        po.line_items.append(
            POLineItem(
                description=li.description,
                quantity=li.quantity,
                unit_price=li.unit_price,
                total=li.total,
            )
        )
    return po


def _opt_uuid(raw: str | None, field: str) -> uuid.UUID | None:
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid {field}")
