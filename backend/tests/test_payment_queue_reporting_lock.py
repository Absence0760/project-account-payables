"""The payment queue must not count an incomplete rate lock as a conversion.

`GET /api/payments/queue` rolls the whole payable set into the org's reporting
currency in SQL and reports `unconverted_count` — how many rows it could NOT
honestly convert and had to take at face value. A fallback nobody reports is
just a wrong number (`docs/decisions.md` §35), so that counter is what keeps a
slightly-mixed total honest.

A rate lock is TWO columns: `Invoice.reporting_amount` and the
`Invoice.reporting_currency` it is denominated in. `reporting_amount_for_row`
requires both. The queue's inline SQL copy tested only the first explicitly and
left the second to `upper(reporting_currency) = tgt` — which, for a row with an
amount but a NULL currency, is NULL rather than FALSE under SQL's three-valued
logic. `NOT NULL` is NULL too, so the row fell through the `unconverted` CASE
and was counted as CONVERTED, while the money it contributed came from the
unlocked face `amount`. A foreign-currency total presented as fully converted.

The fix is not a patch to the copy: `_payment_queue_rollup` now builds the
expressions with `currency_conversion.invoice_reporting_amount_sql`, the one
owner of "is this row locked to the reporting currency?", which states
`reporting_currency IS NOT NULL` explicitly. Two definitions of "converted" is
how this got in.

Requires the dev Postgres (`pnpm db:up`).
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models.invoice import Invoice, InvoiceStatus

pytestmark = pytest.mark.asyncio

TENANT = "a"

# The org reports in USD (the platform default for an org with no setting), so
# a CHF invoice is the foreign row whose conversion has to be accounted for.
FOREIGN = "CHF"


def _user(uid):
    return SimpleNamespace(id=uid, full_name="Lock Tester", roles=["admin"])


def _org(org_id):
    return SimpleNamespace(id=org_id, name="PyTest", slug="pytesta", settings={})


async def _seed(mk, org_id, *, number, currency, amount, rep_amount=None, rep_currency=None):
    async with mk() as s:
        s.add(
            Invoice(
                id=uuid.uuid4(),
                invoice_number=number,
                vendor_name="Lock Vendor",
                amount=Decimal(amount),
                currency=currency,
                status=InvoiceStatus.approved,
                organization_id=org_id,
                correlation_id=uuid.uuid4(),
                reporting_amount=None if rep_amount is None else Decimal(rep_amount),
                reporting_currency=rep_currency,
            )
        )
        await s.commit()


async def _queue(realdb, mk):
    from app.api.payments import payment_queue

    info = realdb.info(TENANT)
    async with mk() as db:
        return await payment_queue(
            db=db, org=_org(info.org_id), user=_user(info.users["admin"]), entity_id=None
        )


async def test_a_lock_amount_without_a_lock_currency_is_not_a_lock(realdb):
    """The regression. A `reporting_amount` with no `reporting_currency` is an
    incomplete lock: the rollup rightly falls back to the face amount, and it
    must SAY it did."""
    mk = realdb.sessionmaker(TENANT)
    org_id = realdb.info(TENANT).org_id
    await _seed(
        mk,
        org_id,
        number="INV-HALFLOCK",
        currency=FOREIGN,
        amount="100.00",
        # A figure on the row with nothing saying what it is denominated in.
        rep_amount="999.00",
        rep_currency=None,
    )

    result = await _queue(realdb, mk)

    assert result["unconverted_count"] == 1, (
        "a reporting_amount with no reporting_currency was counted as converted"
    )
    # And the money is the face amount, never the orphaned figure.
    assert result["total_amount"] == "100.00"
    bucket = next(e for e in result["by_currency"] if e["currency"] == FOREIGN)
    assert bucket["total_amount"] == "100.00"


async def test_a_complete_lock_is_converted_and_not_counted(realdb):
    """The control: both columns present and matching the target currency, so
    the persisted rate-locked figure is used and nothing is flagged."""
    mk = realdb.sessionmaker(TENANT)
    org_id = realdb.info(TENANT).org_id
    await _seed(
        mk,
        org_id,
        number="INV-LOCKED",
        currency=FOREIGN,
        amount="100.00",
        rep_amount="112.50",
        rep_currency="USD",
    )

    result = await _queue(realdb, mk)

    assert result["unconverted_count"] == 0
    assert result["total_amount"] == "112.50"


async def test_a_foreign_row_with_no_lock_at_all_is_still_counted(realdb):
    """The pre-existing behaviour the fix must not disturb — absence of a lock
    has always been flagged; only the HALF-present lock slipped through."""
    mk = realdb.sessionmaker(TENANT)
    org_id = realdb.info(TENANT).org_id
    await _seed(mk, org_id, number="INV-NOLOCK", currency=FOREIGN, amount="100.00")

    result = await _queue(realdb, mk)

    assert result["unconverted_count"] == 1
    assert result["total_amount"] == "100.00"


async def test_a_row_already_in_the_reporting_currency_is_never_flagged(realdb):
    """A USD invoice needs no conversion, so it is not an unconverted row even
    though it carries no lock."""
    mk = realdb.sessionmaker(TENANT)
    org_id = realdb.info(TENANT).org_id
    await _seed(mk, org_id, number="INV-DOMESTIC", currency="USD", amount="100.00")

    result = await _queue(realdb, mk)

    assert result["unconverted_count"] == 0
    assert result["total_amount"] == "100.00"


async def test_the_queue_does_not_restate_the_conversion_rule(realdb):
    """The durable half of the fix. The queue rollup must build its
    reporting-amount expressions from `currency_conversion`, not from its own
    copy of the CASE — two definitions of "converted" is what let a NULL
    currency read as a rate lock."""
    import inspect

    from app.api import payments as payments_api

    source = inspect.getsource(payments_api._payment_queue_rollup)
    assert "invoice_reporting_amount_sql(" in source
    assert "reporting_amount.isnot(" not in source, (
        "the queue rollup is restating the rate-lock test instead of using the shared builder"
    )
