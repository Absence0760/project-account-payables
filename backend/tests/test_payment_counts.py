"""GET /api/payments/counts — per-status tallies span the whole set.

Regression for the History-tab chip undercount: the chip counts were computed
from the loaded (page-1, size-20) payment array, so they missed payments past
the first page. The endpoint tallies every status across the entity-scoped set.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.invoice import Invoice, InvoiceStatus
from app.models.payment import Payment

TENANT = "a"


async def _default_entity_id(s):
    from app.models.entity import Entity

    return (await s.execute(select(Entity.id).where(Entity.is_default))).scalar_one()


@pytest.mark.asyncio
async def test_payment_counts_span_all_pages(realdb):
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    async with mk() as s:
        ent = await _default_entity_id(s)

        # One payment PER invoice: the `uq_payments_one_live_per_invoice` index
        # (one live payment per invoice) forbids stacking many live payments on
        # a single invoice, which is irrelevant to this test — it only needs 28
        # payment rows spanning >1 count page, on any invoices.
        # 25 completed (more than the 20-row list page) + 3 pending.
        def _mk_invoice(n: int) -> Invoice:
            return Invoice(
                organization_id=org_id,
                entity_id=ent,
                invoice_number=f"PCNT-{n}",
                vendor_name="Count Vendor",
                amount=Decimal("10.00"),
                currency="USD",
                status=InvoiceStatus.approved,
            )

        n = 0
        for _ in range(25):
            inv = _mk_invoice(n)
            s.add(inv)
            await s.flush()
            s.add(
                Payment(
                    invoice_id=inv.id, entity_id=ent, amount=Decimal("10.00"), status="completed"
                )
            )
            n += 1
        for _ in range(3):
            inv = _mk_invoice(n)
            s.add(inv)
            await s.flush()
            s.add(
                Payment(invoice_id=inv.id, entity_id=ent, amount=Decimal("5.00"), status="pending")
            )
            n += 1
        await s.commit()

    async with realdb.client(key=TENANT, role="ap_manager") as c:
        resp = await c.get("/api/payments/counts")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["by_status"].get("completed", 0) >= 25
    assert body["by_status"].get("pending", 0) >= 3
    assert body["total"] == sum(body["by_status"].values())
