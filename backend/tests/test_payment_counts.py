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


@pytest.mark.asyncio
async def test_payment_counts_honour_the_lists_population_filters(realdb):
    """The chips must describe the rows the table shows, not the whole tenant.

    `payment_status_counts` declared no query parameters at all and grouped over
    the entire entity-scoped set, so a History search for one vendor left the
    chips reading the tenant's total over a one-row table — the same defect
    `GET /api/invoices/counts` closed in #352, on a sibling surface the
    OpenAPI-driven rollup guard doesn't discover (it ends in `/counts`, not
    `/summary`). The frontend sends a live `search` on `GET /api/payments`, so
    this was reachable on screen.
    """
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    async with mk() as s:
        ent = await _default_entity_id(s)

        def _add(number: str, vendor: str, status: str, amount: str, ref: str | None = None):
            inv = Invoice(
                organization_id=org_id,
                entity_id=ent,
                invoice_number=number,
                vendor_name=vendor,
                amount=Decimal(amount),
                currency="USD",
                status=InvoiceStatus.approved,
            )
            s.add(inv)
            return inv

        globex = _add("PFILT-1", "Globex Industrial", "completed", "100.00")
        initech_a = _add("PFILT-2", "Initech", "completed", "200.00")
        initech_b = _add("PFILT-3", "Initech", "pending", "300.00")
        await s.flush()
        s.add(
            Payment(
                invoice_id=globex.id,
                entity_id=ent,
                amount=Decimal("100.00"),
                status="completed",
                method="ach",
                reference="GLOBEX-REF",
            )
        )
        s.add(
            Payment(
                invoice_id=initech_a.id,
                entity_id=ent,
                amount=Decimal("200.00"),
                status="completed",
                method="wire",
            )
        )
        s.add(
            Payment(
                invoice_id=initech_b.id,
                entity_id=ent,
                amount=Decimal("300.00"),
                status="pending",
                method="wire",
            )
        )
        await s.commit()

    async with realdb.client(key=TENANT, role="ap_manager") as c:
        # The exact interleaving a user sees: the same term on both requests.
        listed = await c.get("/api/payments?search=globex")
        counted = await c.get("/api/payments/counts?search=globex")
        assert listed.status_code == 200, listed.text
        assert counted.status_code == 200, counted.text
        # The chips' "All" is the list's own total — pre-fix the tenant's whole
        # payment count answered here instead.
        assert counted.json()["total"] == listed.json()["total"] == 1
        assert counted.json()["by_status"] == {"completed": 1}

        # A vendor with rows in two statuses: both chips narrow together.
        listed = await c.get("/api/payments?search=initech")
        counted = await c.get("/api/payments/counts?search=initech")
        assert counted.json()["total"] == listed.json()["total"] == 2
        assert counted.json()["by_status"] == {"completed": 1, "pending": 1}

        # The search leg reaches `Payment.reference`, like the list's does.
        counted = await c.get("/api/payments/counts?search=GLOBEX-REF")
        assert counted.json()["total"] == 1

        # Every other population filter the list accepts, one at a time.
        assert (await c.get("/api/payments/counts?method=wire")).json()["by_status"] == {
            "completed": 1,
            "pending": 1,
        }
        assert (await c.get("/api/payments/counts?amount_min=250")).json()["total"] == 1
        assert (await c.get("/api/payments/counts?amount_max=150")).json()["total"] == 1
        assert (await c.get("/api/payments/counts?search=initech&amount_min=250")).json()[
            "total"
        ] == 1

        # A filter matching nothing is an empty tally, not the whole set and not
        # a 500 — GROUP BY over no rows returns no rows.
        empty = await c.get("/api/payments/counts?search=no-such-vendor-anywhere")
        assert empty.status_code == 200
        assert empty.json() == {"total": 0, "by_status": {}}


@pytest.mark.asyncio
async def test_payment_counts_ignore_a_status_param(realdb):
    """`status` is the dimension being tallied, so the chips must not apply it.

    Applying it would zero every chip but the selected one — the rule
    `invoices.py::invoice_counts` and `purchase_orders.py` both state. It must
    hold even when a real population filter rides alongside it.
    """
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    async with mk() as s:
        ent = await _default_entity_id(s)
        for n, status in (("PSTAT-1", "completed"), ("PSTAT-2", "pending")):
            inv = Invoice(
                organization_id=org_id,
                entity_id=ent,
                invoice_number=n,
                vendor_name="Statusco",
                amount=Decimal("50.00"),
                currency="USD",
                status=InvoiceStatus.approved,
            )
            s.add(inv)
            await s.flush()
            s.add(Payment(invoice_id=inv.id, entity_id=ent, amount=Decimal("50.00"), status=status))
        await s.commit()

    async with realdb.client(key=TENANT, role="ap_manager") as c:
        plain = (await c.get("/api/payments/counts?search=statusco")).json()
        with_status = (await c.get("/api/payments/counts?search=statusco&status=completed")).json()
    assert plain["by_status"] == {"completed": 1, "pending": 1}
    assert with_status == plain, "a status param must not narrow the tally it produces"


@pytest.mark.asyncio
async def test_payment_counts_and_the_list_share_one_filter_builder(realdb):
    """Structural: two independently-maintained filter blocks is the drift.

    `list_payments` restated its whole filter set twice (rows, then a
    fan-out-free count) and `/counts` had a third, empty one. This asserts both
    handlers route through `_payment_list_filters`, so a new filter reaches the
    chips for free instead of being added to one of three places.
    """
    import inspect

    from app.api import payments as payments_mod

    for handler in (payments_mod.list_payments, payments_mod.payment_status_counts):
        src = inspect.getsource(handler)
        assert "_payment_list_filters(" in src, (
            f"{handler.__name__} does not route through the shared filter builder"
        )
        # And no handler re-implements the search predicate inline.
        assert "ilike_contains(Invoice.vendor_name" not in src, (
            f"{handler.__name__} restates the search predicate instead of sharing it"
        )
