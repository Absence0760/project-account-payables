"""A requisition's header total is the sum of the lines it actually stored.

``RequisitionLineItem.quantity`` is ``Numeric(12, 4)`` and ``unit_price``
``Numeric(15, 2)``, so ``quantity * unit_price`` can carry six decimal places —
but both ``line_items.total`` and ``purchase_requisitions.total`` are
``Numeric(15, 2)``. Postgres therefore rounded **each line** on the way in while
``recompute_total`` summed the UNROUNDED products, so the header was the
rounding of a sum and the lines were a sum of roundings. Those are not the same
number, and the gap grows with the line count.

The drift does not stop at the requisition: ``convert_requisition_to_po`` copies
the header figure onto the ``PurchaseOrder`` while each ``POLineItem`` carries
the rounded line figure, so the PO that ``po_matching`` runs its tolerance gate
against did not equal its own lines either.

DO NOT run this file standalone in a concurrent build — the ``realdb`` fixture
truncates all tables sequentially. The orchestrator runs the suite at the end.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.procurement import POLineItem, PurchaseOrder, PurchaseRequisition
from app.services.requisition_service import line_total

TENANT = "a"

#: 1.5 x 10.01 = 15.015 — exactly the half-cent a Numeric(15, 2) column must
#: round, and a quantity/price pair a real buyer types (a metre-and-a-half of
#: something priced per metre).
_QTY = "1.5"
_PRICE = "10.01"
_LINES = 12


def _num() -> str:
    return f"REQROUND-{uuid.uuid4().hex[:8]}"


def _payload() -> dict:
    return {
        "requisition_number": _num(),
        "title": "Cable, per metre",
        "line_items": [
            {"description": f"Reel {i}", "quantity": _QTY, "unit_price": _PRICE}
            for i in range(_LINES)
        ],
    }


def test_line_total_is_quantized_to_the_precision_it_is_stored_at():
    """The pure helper returns what the column will hold, not a 6dp product."""
    assert line_total(Decimal(_QTY), Decimal(_PRICE)) == Decimal("15.02")
    # Half-up, matching the rest of the money path (`ROUND_HALF_UP` everywhere).
    assert line_total(Decimal("1"), Decimal("0.005")) == Decimal("0.01")
    # A line with no money still carries none.
    assert line_total(None, Decimal(_PRICE)) is None
    assert line_total(Decimal(_QTY), None) is None


@pytest.mark.asyncio
async def test_requisition_header_total_equals_its_persisted_lines(realdb):
    mk = realdb.sessionmaker(TENANT)
    async with realdb.client(key=TENANT, role="ap_clerk") as c:
        created = await c.post("/api/requisitions", json=_payload())
    assert created.status_code == 201, created.text
    req_id = uuid.UUID(created.json()["id"])

    async with mk() as s:
        req = (
            await s.execute(
                select(PurchaseRequisition)
                .options(selectinload(PurchaseRequisition.line_items))
                .where(PurchaseRequisition.id == req_id)
            )
        ).scalar_one()
        line_sum = sum((li.total for li in req.line_items), Decimal("0"))
        # Pre-fix: header 180.18 (the rounding of 12 x 15.015) vs lines 180.24
        # (12 roundings of 15.015) — six cents apart, growing with line count.
        assert req.total == line_sum
        assert req.total == Decimal("180.24")


@pytest.mark.asyncio
async def test_converted_po_total_equals_its_own_lines(realdb):
    """The PO inherits the header figure and the rounded lines — so a drifting
    header lands on the document `po_matching` runs its tolerance gate against."""
    mk = realdb.sessionmaker(TENANT)
    async with realdb.client(key=TENANT, role="ap_clerk") as c:
        req_id = (await c.post("/api/requisitions", json=_payload())).json()["id"]
        await c.post(f"/api/requisitions/{req_id}/submit")
    async with realdb.client(key=TENANT, role="ap_manager") as c:
        await c.post(f"/api/requisitions/{req_id}/approve")
        converted = await c.post(f"/api/requisitions/{req_id}/convert-to-po")
    assert converted.status_code == 200, converted.text
    po_id = uuid.UUID(converted.json()["po_id"])

    async with mk() as s:
        po = (await s.execute(select(PurchaseOrder).where(PurchaseOrder.id == po_id))).scalar_one()
        po_lines = (
            (await s.execute(select(POLineItem.total).where(POLineItem.po_id == po_id)))
            .scalars()
            .all()
        )
        assert po.total == sum(po_lines, Decimal("0"))
        assert po.total == Decimal("180.24")
