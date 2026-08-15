"""`POST /api/payments` must net applied credit memos, like the run path does.

`services/payment_runs.py` has netted applied credit memos off what a payment
run actually pays since the netting fix landed (`inv.amount - already_applied`).
The standalone endpoint never did: it bound the payment to a bare
`invoice.amount` and 422'd any other figure — so a credited invoice paid the
vendor the FULL pre-credit amount there, and a caller who knew the correct net
figure could not even submit it. Every guard around *applying* a memo (vendor
match, currency match, no over-application) was solid and none of it mattered
on this path.

Both paths now go through the one helper,
`services/payment_runs.net_payable_amount`, so they can't disagree about what
an invoice is worth.

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


async def _seed_vendor_and_approved_invoice(
    mk, org_id, *, number: str, amount: Decimal
) -> tuple[str, str]:
    async with mk() as s:
        vendor = Vendor(organization_id=org_id, name="Standalone Netting Vendor")
        s.add(vendor)
        await s.flush()
        inv = Invoice(
            organization_id=org_id,
            invoice_number=number,
            vendor_name=vendor.name,
            vendor_id=vendor.id,
            amount=amount,
            currency="USD",
            status=InvoiceStatus.approved,
        )
        s.add(inv)
        await s.commit()
        return str(vendor.id), str(inv.id)


async def _apply_memo(client, *, vendor_id: str, invoice_id: str, amount: str, number: str):
    r = await client.post(
        "/api/credit-memos",
        json={
            "memo_number": number,
            "vendor_id": vendor_id,
            "amount": amount,
            "invoice_id": invoice_id,
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "applied"


async def test_standalone_payment_is_netted_against_applied_credit_memos(realdb):
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    vendor_id, invoice_id = await _seed_vendor_and_approved_invoice(
        mk, org_id, number="STANDALONE-NET-1", amount=Decimal("1000.00")
    )

    async with realdb.client(key=TENANT, role="admin") as c:
        await _apply_memo(
            c,
            vendor_id=vendor_id,
            invoice_id=invoice_id,
            amount="300.00",
            number="CM-STANDALONE-1",
        )
        r = await c.post("/api/payments", json={"invoice_id": invoice_id, "method": "ach"})

    assert r.status_code == 201, r.text
    # $1000.00 invoice - $300.00 applied credit = $700.00 actually paid.
    # (`MoneyAmount` serialises to a JSON number by design — see
    # `schemas/money.py`; the exact Decimal is asserted on the row below.)
    assert r.json()["amount"] == 700.00, r.json()

    async with mk() as s:
        payment = await s.get(Payment, uuid.UUID(r.json()["id"]))
        assert payment.amount == Decimal("700.00")


async def test_the_net_amount_is_the_one_the_caller_may_submit(realdb):
    """The cross-check compares against the NET figure. Sending the gross
    (pre-credit) amount is exactly the mistake the 422 should catch, and the
    net figure — previously rejected — must be accepted."""
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    vendor_id, invoice_id = await _seed_vendor_and_approved_invoice(
        mk, org_id, number="STANDALONE-NET-2", amount=Decimal("1000.00")
    )

    async with realdb.client(key=TENANT, role="admin") as c:
        await _apply_memo(
            c,
            vendor_id=vendor_id,
            invoice_id=invoice_id,
            amount="250.00",
            number="CM-STANDALONE-2",
        )
        gross = await c.post(
            "/api/payments",
            json={"invoice_id": invoice_id, "amount": "1000.00", "method": "ach"},
        )
        assert gross.status_code == 422, gross.text

        net = await c.post(
            "/api/payments",
            json={"invoice_id": invoice_id, "amount": "750.00", "method": "ach"},
        )
    assert net.status_code == 201, net.text
    assert net.json()["amount"] == 750.00

    async with mk() as s:
        payment = await s.get(Payment, uuid.UUID(net.json()["id"]))
        assert payment.amount == Decimal("750.00")


async def test_a_fully_credited_invoice_cannot_be_paid(realdb):
    """Nothing is owed, so there is no payment to book — a zero-amount row
    would be a money record for money that never moves."""
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    vendor_id, invoice_id = await _seed_vendor_and_approved_invoice(
        mk, org_id, number="STANDALONE-NET-3", amount=Decimal("500.00")
    )

    async with realdb.client(key=TENANT, role="admin") as c:
        await _apply_memo(
            c,
            vendor_id=vendor_id,
            invoice_id=invoice_id,
            amount="500.00",
            number="CM-STANDALONE-3",
        )
        r = await c.post("/api/payments", json={"invoice_id": invoice_id, "method": "ach"})

    assert r.status_code == 409, r.text
    assert "credit" in r.json()["detail"].lower()


async def test_an_uncredited_invoice_still_pays_its_full_amount(realdb):
    """The common case is unchanged: no memos → net == gross."""
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    _, invoice_id = await _seed_vendor_and_approved_invoice(
        mk, org_id, number="STANDALONE-NET-4", amount=Decimal("420.00")
    )

    async with realdb.client(key=TENANT, role="admin") as c:
        r = await c.post("/api/payments", json={"invoice_id": invoice_id, "method": "ach"})
    assert r.status_code == 201, r.text
    assert r.json()["amount"] == 420.00

    async with mk() as s:
        payment = await s.get(Payment, uuid.UUID(r.json()["id"]))
        assert payment.amount == Decimal("420.00")
