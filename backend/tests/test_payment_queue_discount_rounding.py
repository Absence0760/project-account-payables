"""The payment queue's early-pay savings must round the same way twice.

`GET /api/payments/queue` reports the same discount twice on one screen: a
per-row `discount_amount` for each invoice, and a whole-set `total_savings`
(plus a per-currency `by_currency[].total_savings`) computed by
`_payment_queue_rollup`. Both derive from the SAME `PaymentSchedule` row —
`invoice.amount * discount_percent / 100`, rounded to cents.

They rounded it differently. The row used `Decimal.quantize(Decimal("0.01"))`,
which takes the decimal CONTEXT's default of `ROUND_HALF_EVEN` (banker's
rounding), while the rollup computes the figure in SQL with
`round(numeric, 2)`, which Postgres defines as half-away-from-zero. On a
genuine half-cent — a 21.00 invoice at 2.50%, i.e. exactly 0.5250 — the row
rendered `0.52` and the banner above it summed `0.53`.

Latent, because nothing in the app writes a `PaymentSchedule` today (only
`scripts/seed.py` does), which is exactly why it needs a test rather than a
comment: the day a schedule-writing feature lands, the two figures disagree
silently on a screen an AP manager reads before committing cash.

Half-away-from-zero is the mode both sides now use: it is what Postgres does,
what every other money quantizer in this codebase passes explicitly
(`international_payments._quantize_money`, `payment_settlement._q`,
`currency_conversion._quantize_money`), and what auditors expect. Money here is
non-negative, so `ROUND_HALF_UP` and half-away-from-zero are the same rule.

Requires the dev Postgres (`pnpm db:up`).
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from app.models.invoice import Invoice, InvoiceStatus
from app.models.payment import PaymentSchedule

pytestmark = pytest.mark.asyncio

TENANT = "a"

# A genuine half-cent: 21.00 * 2.50 / 100 == 0.5250 exactly.
# ROUND_HALF_EVEN -> 0.52 (round to even); half-away-from-zero -> 0.53.
HALF_CENT_AMOUNT = Decimal("21.00")
HALF_CENT_PERCENT = Decimal("2.50")
EXPECTED_SAVINGS = Decimal("0.53")

# A currency of its own, so the per-currency rollup bucket under test carries
# only this invoice regardless of what else a fixture seeded.
CURRENCY = "CHF"


def _user(uid):
    return SimpleNamespace(id=uid, full_name="Rounding Tester", roles=["admin"])


def _org(org_id):
    return SimpleNamespace(id=org_id, name="PyTest", slug="pytesta", settings={})


async def _seed_discounted_invoice(mk, org_id) -> uuid.UUID:
    inv_id = uuid.uuid4()
    async with mk() as s:
        s.add(
            Invoice(
                id=inv_id,
                invoice_number="INV-HALFCENT",
                vendor_name="Half Cent Vendor",
                amount=HALF_CENT_AMOUNT,
                currency=CURRENCY,
                status=InvoiceStatus.approved,
                organization_id=org_id,
                correlation_id=uuid.uuid4(),
            )
        )
        s.add(
            PaymentSchedule(
                id=uuid.uuid4(),
                invoice_id=inv_id,
                due_date=date.today() + timedelta(days=30),
                discount_date=date.today() + timedelta(days=10),
                discount_percent=HALF_CENT_PERCENT,
                payment_terms="2.5/10 net 30",
            )
        )
        await s.commit()
    return inv_id


async def test_the_row_and_the_rollup_agree_on_a_half_cent(realdb):
    """The regression: one discount, one screen, two numbers."""
    from app.api.payments import payment_queue

    mk = realdb.sessionmaker(TENANT)
    org_id = realdb.info(TENANT).org_id
    await _seed_discounted_invoice(mk, org_id)

    async with mk() as db:
        result = await payment_queue(
            db=db,
            org=_org(org_id),
            user=_user(realdb.info(TENANT).users["admin"]),
            entity_id=None,
        )

    row = next(i for i in result["items"] if i["invoice_number"] == "INV-HALFCENT")
    bucket = next(e for e in result["by_currency"] if e["currency"] == CURRENCY)

    # Money crosses the JSON boundary as an exact decimal string, never a float.
    assert row["discount_amount"] == str(EXPECTED_SAVINGS)
    assert bucket["total_savings"] == str(EXPECTED_SAVINGS)
    assert row["discount_amount"] == bucket["total_savings"]


async def test_the_python_row_matches_what_postgres_would_round_it_to(realdb):
    """Pins the two implementations to each other rather than to a literal, so
    a future change to either side has to move both.

    `round(numeric, 2)` is the exact expression `_payment_queue_rollup` sums.
    """
    from app.api.payments import payment_queue

    mk = realdb.sessionmaker(TENANT)
    org_id = realdb.info(TENANT).org_id
    inv_id = await _seed_discounted_invoice(mk, org_id)

    async with mk() as db:
        pg_rounded = (
            await db.execute(
                text(
                    "SELECT round(i.amount * s.discount_percent / 100, 2) "
                    "FROM invoices i JOIN payment_schedules s ON s.invoice_id = i.id "
                    "WHERE i.id = :inv"
                ),
                {"inv": inv_id},
            )
        ).scalar_one()

        result = await payment_queue(
            db=db,
            org=_org(org_id),
            user=_user(realdb.info(TENANT).users["admin"]),
            entity_id=None,
        )

    row = next(i for i in result["items"] if i["invoice_number"] == "INV-HALFCENT")
    assert Decimal(row["discount_amount"]) == Decimal(pg_rounded)


async def test_a_non_half_cent_discount_is_unchanged(realdb):
    """The fix only moves the tie-break: an ordinary discount rounds as it
    always did."""
    from app.api.payments import payment_queue

    mk = realdb.sessionmaker(TENANT)
    org_id = realdb.info(TENANT).org_id
    inv_id = uuid.uuid4()
    async with mk() as s:
        s.add(
            Invoice(
                id=inv_id,
                invoice_number="INV-ORDINARY",
                vendor_name="Ordinary Vendor",
                amount=Decimal("1000.00"),
                currency=CURRENCY,
                status=InvoiceStatus.approved,
                organization_id=org_id,
                correlation_id=uuid.uuid4(),
            )
        )
        s.add(
            PaymentSchedule(
                id=uuid.uuid4(),
                invoice_id=inv_id,
                due_date=date.today() + timedelta(days=30),
                discount_date=date.today() + timedelta(days=10),
                discount_percent=Decimal("2.00"),
            )
        )
        await s.commit()

    async with mk() as db:
        result = await payment_queue(
            db=db,
            org=_org(org_id),
            user=_user(realdb.info(TENANT).users["admin"]),
            entity_id=None,
        )

    row = next(i for i in result["items"] if i["invoice_number"] == "INV-ORDINARY")
    bucket = next(e for e in result["by_currency"] if e["currency"] == CURRENCY)
    assert row["discount_amount"] == "20.00"
    assert bucket["total_savings"] == "20.00"
