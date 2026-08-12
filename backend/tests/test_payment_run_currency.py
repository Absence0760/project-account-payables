"""`POST /api/payments/runs` (and the AI Cash-Flow Copilot's draft-run route,
which shares `services.payment_runs.create_payment_run_for_invoices` verbatim)
must refuse to batch invoices of different currencies into one run.

`PaymentRun.total_amount` is a single bare `Numeric` column with no currency
of its own, and the CFO-approval-threshold check compares it against a bare
org-wide number. Before this fix, `create_payment_run_for_invoices` summed
`net_amount` across every invoice in the batch regardless of currency — a
$6,000 USD invoice and a €6,000 EUR invoice became one meaningless "$12,000"
total, which could misfire (or fail to fire) the CFO gate on a face-value
coincidence across currencies. Same currency-mixing bug class as
`services/budget_service.py` (f1224025) and this session's sibling fixes in
`structuring.py` / `contract_spend.py` / `invoice_warnings.py`.

Runs against the opt-in `realdb` fixture (skips without `pnpm db:up`).
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.entity import Entity
from app.models.invoice import Invoice, InvoiceStatus
from app.models.vendor import Vendor

pytestmark = pytest.mark.asyncio


async def _default_entity_id(session, org_id):
    return (
        await session.execute(
            select(Entity.id).where(Entity.organization_id == org_id, Entity.is_default)
        )
    ).scalar_one()


async def _seed_vendor_and_invoice(mk, org_id, entity_id, *, number, amount, currency):
    async with mk() as s:
        vendor = Vendor(organization_id=org_id, name="Currency Test Vendor")
        s.add(vendor)
        await s.flush()
        inv = Invoice(
            organization_id=org_id,
            entity_id=entity_id,
            invoice_number=number,
            vendor_name=vendor.name,
            vendor_id=vendor.id,
            amount=Decimal(amount),
            currency=currency,
            status=InvoiceStatus.approved,
        )
        s.add(inv)
        await s.commit()
        await s.refresh(inv)
        return str(inv.id)


async def test_payment_run_refuses_mixed_currency_invoices(realdb):
    info = realdb.info("a")
    mk = realdb.sessionmaker("a")
    async with mk() as s:
        ent = await _default_entity_id(s, info.org_id)

    usd_id = await _seed_vendor_and_invoice(
        mk, info.org_id, ent, number="MIX-USD", amount="6000.00", currency="USD"
    )
    eur_id = await _seed_vendor_and_invoice(
        mk, info.org_id, ent, number="MIX-EUR", amount="6000.00", currency="EUR"
    )

    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post(
            "/api/payments/runs",
            json={"items": [{"invoice_id": usd_id}, {"invoice_id": eur_id}]},
        )
    assert resp.status_code == 422, resp.text
    assert "currency" in resp.json()["detail"].lower()

    # No PaymentRun/Payment rows must have been persisted on the refused path.
    async with mk() as s:
        from app.models.payment import Payment, PaymentRun

        runs = (
            (await s.execute(select(PaymentRun).where(PaymentRun.organization_id == info.org_id)))
            .scalars()
            .all()
        )
        assert not any(r.total_amount == Decimal("12000.00") for r in runs)
        payments = (
            (await s.execute(select(Payment).where(Payment.invoice_id.in_([usd_id, eur_id]))))
            .scalars()
            .all()
        )
        assert payments == []


async def test_payment_run_allows_same_currency_invoices(realdb):
    """The new guard must not reject the ordinary, same-currency case."""
    info = realdb.info("a")
    mk = realdb.sessionmaker("a")
    async with mk() as s:
        ent = await _default_entity_id(s, info.org_id)

    inv_a = await _seed_vendor_and_invoice(
        mk, info.org_id, ent, number="SAME-1", amount="300.00", currency="USD"
    )
    inv_b = await _seed_vendor_and_invoice(
        mk, info.org_id, ent, number="SAME-2", amount="200.00", currency="USD"
    )

    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post(
            "/api/payments/runs",
            json={"items": [{"invoice_id": inv_a}, {"invoice_id": inv_b}]},
        )
    assert resp.status_code == 201, resp.text
    assert resp.json()["total_amount"] == "500.00"
    assert resp.json()["payment_count"] == 2
