"""Standalone POST /payments must book the APPROVED invoice amount.

The handler used to write `amount=body.amount` — the caller's value — so an
actor could record a $99,999 payment against a $500 approved invoice, leaving
the approved amount and the paid amount permanently divergent. The payment
amount is now bound to the invoice; a supplied value must match it (else 422).

All DB-backed via `realdb` (requires the dev Postgres; skips otherwise).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.invoice import Invoice, InvoiceStatus
from app.models.payment import Payment

TENANT = "a"


async def _default_entity_id(s):
    from app.models.entity import Entity

    return (await s.execute(select(Entity.id).where(Entity.is_default))).scalar_one()


async def _seed_approved_invoice(mk, org_id, amount=Decimal("500.00")) -> uuid.UUID:
    async with mk() as s:
        ent = await _default_entity_id(s)
        inv = Invoice(
            organization_id=org_id,
            entity_id=ent,
            invoice_number=f"PAY-{uuid.uuid4().hex[:8]}",
            vendor_name="Pay Vendor",
            amount=amount,
            currency="USD",
            status=InvoiceStatus.approved,
        )
        s.add(inv)
        await s.commit()
        return inv.id


@pytest.mark.asyncio
async def test_overpay_amount_is_rejected(realdb):
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    inv_id = await _seed_approved_invoice(mk, org_id, Decimal("500.00"))

    async with realdb.client(key=TENANT, role="admin") as client:
        resp = await client.post(
            "/api/payments",
            json={"invoice_id": str(inv_id), "amount": "99999.00", "method": "ach"},
        )
    assert resp.status_code == 422, resp.text

    # No Payment row should have been created.
    async with mk() as s:
        rows = (
            (await s.execute(select(Payment).where(Payment.invoice_id == inv_id))).scalars().all()
        )
        assert rows == []


@pytest.mark.asyncio
async def test_matching_amount_books_the_invoice_amount(realdb):
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    inv_id = await _seed_approved_invoice(mk, org_id, Decimal("500.00"))

    async with realdb.client(key=TENANT, role="admin") as client:
        resp = await client.post(
            "/api/payments",
            json={"invoice_id": str(inv_id), "amount": "500.00", "method": "ach"},
        )
    assert resp.status_code == 201, resp.text

    async with mk() as s:
        pay = (await s.execute(select(Payment).where(Payment.invoice_id == inv_id))).scalar_one()
        # The stored amount is the invoice amount — exact Decimal, never the wire value.
        assert pay.amount == Decimal("500.00")
