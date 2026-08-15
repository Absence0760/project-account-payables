"""`Payment.settled_amount` — what the rail moved, beside what AP authorized.

`payment_settlement.verify_settlement` could already tell that a processor
settled a different figure than we instructed. It could not RECORD it: the row
carried one `amount` (the authorization) and a terminal-or-not status, with no
representation of "settled for less than authorized". An under-settlement left
the payment legitimately `completed` and the invoice `paid`, so the ERP, the
aging report and the 1099 YTD totals all read it as settled in full while the
vendor was short.

These tests pin the two properties the rest of the feature stands on:

  * the column is EXACT (`Numeric(15, 2)`) — a settled figure compared against
    an authorized one at cent tolerance cannot round-trip through a float; and
  * NULL is preserved as NULL, never coerced to zero. NULL means "no processor
    ever reported a figure" (an amount-free rail like Dwolla, or a row
    predating migration 0083), and the coverage classifier reads it as
    "nothing indicates a shortfall". If NULL ever became `0`, every such
    payment would look like a total shortfall and hold its invoice forever.

DB-backed via `realdb` (requires the dev Postgres; skips otherwise) because the
point is what POSTGRES stores, not what Python holds — a `Float` column would
pass an in-memory assertion and fail here.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.entity import Entity
from app.models.invoice import Invoice, InvoiceStatus
from app.models.payment import Payment

TENANT = "a"


async def _seed_payment(mk, org_id, **payment_kwargs) -> uuid.UUID:
    async with mk() as s:
        ent = (await s.execute(select(Entity.id).where(Entity.is_default))).scalar_one()
        inv = Invoice(
            organization_id=org_id,
            entity_id=ent,
            invoice_number=f"SETL-{uuid.uuid4().hex[:8]}",
            vendor_name="Settled Vendor",
            amount=Decimal("500.00"),
            currency="USD",
            status=InvoiceStatus.payment_scheduled,
        )
        s.add(inv)
        await s.flush()
        # `Payment` is scoped by its invoice + entity, not by an org column
        # (unlike `PaymentRun`).
        pay = Payment(
            entity_id=ent,
            invoice_id=inv.id,
            amount=Decimal("500.00"),
            method="ach",
            status="completed",
            **payment_kwargs,
        )
        s.add(pay)
        await s.commit()
        return pay.id


@pytest.mark.asyncio
async def test_settled_amount_round_trips_exactly(realdb):
    """A figure with cents survives the round trip bit-for-bit.

    `0.10 + 0.20 != 0.30` in binary floating point; this value is chosen so a
    `Float` column would come back as something other than what went in.
    """
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    pay_id = await _seed_payment(
        mk,
        org_id,
        settled_amount=Decimal("249.99"),
        settled_currency="USD",
    )

    async with mk() as s:
        row = (await s.execute(select(Payment).where(Payment.id == pay_id))).scalar_one()
        assert row.settled_amount == Decimal("249.99")
        assert isinstance(row.settled_amount, Decimal)
        # Exact string form, not just numeric equality — `Numeric(15, 2)` keeps
        # the scale, which is what makes a cent-tolerance comparison meaningful.
        assert str(row.settled_amount) == "249.99"
        assert row.settled_currency == "USD"


@pytest.mark.asyncio
async def test_unreported_settlement_stays_null_not_zero(realdb):
    """An amount-free rail leaves NULL, and NULL is not zero.

    This is the property that keeps Dwolla (and every pre-0083 row) from
    reading as a total shortfall.
    """
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    pay_id = await _seed_payment(mk, org_id)

    async with mk() as s:
        row = (await s.execute(select(Payment).where(Payment.id == pay_id))).scalar_one()
        assert row.settled_amount is None
        assert row.settled_currency is None
