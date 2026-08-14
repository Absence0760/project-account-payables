"""The live-payment 409 has to say WHICH invoice blocked the run.

`uq_payments_one_live_per_invoice` is the DB-level backstop that stops one
invoice from carrying two live payments. When a payment run trips it, the
operator gets a 409 — and the message used to be "One or more invoices already
have a live payment scheduled.", which identifies nothing. On a forty-invoice
Friday run that leaves bisecting the selection by hand as the only way forward.

Naming them needs the session to still be usable after the `IntegrityError`,
which is why the insert now always runs inside a savepoint (it used to be
wrapped only on the copilot's `plan_id` path). Invoice NUMBER is the identifier
the operator selected the row by, and carries no PII.

Runs against the opt-in `realdb` fixture (skips without `pnpm db:up`).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from app.models.invoice import Invoice, InvoiceStatus
from app.models.payment import Payment
from app.models.vendor import Vendor

pytestmark = pytest.mark.asyncio

TENANT = "a"


async def _seed_approved_invoice(mk, org_id, *, number: str) -> str:
    async with mk() as s:
        vendor = Vendor(organization_id=org_id, name=f"Conflict Vendor {number}")
        s.add(vendor)
        await s.flush()
        inv = Invoice(
            organization_id=org_id,
            invoice_number=number,
            vendor_name=vendor.name,
            vendor_id=vendor.id,
            amount=Decimal("100.00"),
            currency="USD",
            status=InvoiceStatus.approved,
        )
        s.add(inv)
        await s.commit()
        return str(inv.id)


async def _book_live_payment(mk, invoice_id: str, *, status: str = "submitted") -> None:
    async with mk() as s:
        s.add(
            Payment(
                invoice_id=uuid.UUID(invoice_id),
                amount=Decimal("100.00"),
                method="ach",
                status=status,
                correlation_id=uuid.uuid4(),
            )
        )
        await s.commit()


async def test_the_409_names_the_invoice_holding_the_live_payment(realdb):
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)

    clean_id = await _seed_approved_invoice(mk, org_id, number="CONFLICT-CLEAN-1")
    blocked_id = await _seed_approved_invoice(mk, org_id, number="CONFLICT-BLOCKED-1")
    await _book_live_payment(mk, blocked_id)

    async with realdb.client(key=TENANT, role="admin") as c:
        r = await c.post(
            "/api/payments/runs",
            json={
                "items": [
                    {"invoice_id": clean_id, "method": "ach"},
                    {"invoice_id": blocked_id, "method": "ach"},
                ]
            },
        )

    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    # The one that blocked it is named; the innocent one is not.
    assert "CONFLICT-BLOCKED-1" in detail, detail
    assert "CONFLICT-CLEAN-1" not in detail, detail


async def test_every_offending_invoice_is_named(realdb):
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)

    first = await _seed_approved_invoice(mk, org_id, number="CONFLICT-MULTI-A")
    second = await _seed_approved_invoice(mk, org_id, number="CONFLICT-MULTI-B")
    await _book_live_payment(mk, first, status="pending")
    await _book_live_payment(mk, second, status="completed")

    async with realdb.client(key=TENANT, role="admin") as c:
        r = await c.post(
            "/api/payments/runs",
            json={
                "items": [
                    {"invoice_id": first, "method": "ach"},
                    {"invoice_id": second, "method": "ach"},
                ]
            },
        )

    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert "CONFLICT-MULTI-A" in detail, detail
    assert "CONFLICT-MULTI-B" in detail, detail


async def test_a_terminal_payment_does_not_block_or_get_named(realdb):
    """`voided` / `failed` / `cancelled` are outside the partial index, so a
    fresh run is legitimate — and nothing should be reported."""
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)

    invoice_id = await _seed_approved_invoice(mk, org_id, number="CONFLICT-VOIDED-1")
    await _book_live_payment(mk, invoice_id, status="voided")

    async with realdb.client(key=TENANT, role="admin") as c:
        r = await c.post(
            "/api/payments/runs",
            json={"items": [{"invoice_id": invoice_id, "method": "ach"}]},
        )
    assert r.status_code == 201, r.text
