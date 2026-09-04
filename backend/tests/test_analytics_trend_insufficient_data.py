"""Two analytics surfaces that reported a comfortable number where they had no
number at all, plus the malformed-input 500 that sat next to one of them.

  1. `compute_fraud_rate_trend` divided exceptions by invoices and returned
     `Decimal("0")` — a CLEAN fraud rate — for a month with zero invoices.
     `compute_cash_conversion_cycle` returns `None` for exactly that shape.
     The worst case is a month with no invoices booked but exceptions raised
     anyway: the chart drew its most reassuring bar over the one period that
     most warrants a look. `docs/decisions.md` §34 — "cannot attest" must
     never render as "yes".

  2. `POST /api/analytics/forecast_variance` summed the raw `Payment.amount`
     column (denominated in the INVOICE's currency) into an `actual` compared
     against a forecast the CFO typed in ONE currency, and blew up with a bare
     `ValueError` -> 500 on a caller-supplied `month` like `"2026-13"`: the
     guarded parse covered `int(...)` but not the `date(...)` it fed.

DO NOT run this file standalone in a concurrent build — the ``realdb`` fixture
truncates all tables sequentially.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.entity import Entity
from app.models.invoice import Invoice, InvoiceStatus
from app.models.payment import Payment
from app.services.analytics import compute_fraud_rate_trend
from app.utils.dates import utc_today

TENANT = "a"

_EUR_FACE = Decimal("1000.00")
_EUR_AS_USD = Decimal("1086.96")
_USD_FACE = Decimal("1000.00")


# ---------------------------------------------------------------------------
# 1. fraud-rate trend — an empty denominator is not a zero rate
# ---------------------------------------------------------------------------


def test_fraud_rate_trend_zero_invoices_with_exceptions_is_none_not_zero():
    """The exact reassuring-zero shape: no invoices booked, exceptions raised.

    Pre-fix this returned `Decimal("0")` — the single best-looking value on
    the chart — for the period carrying the most alarming facts. The rate is
    NOT COMPUTABLE, so it must be `None`.
    """
    out = compute_fraud_rate_trend(
        [{"month": "2026-05", "invoice_count": 0, "exception_count": 7, "by_type": {}}]
    )
    assert out[0]["rate_pct"] is None
    assert out[0]["insufficient_data"] is True
    # The evidence that made it not-computable stays on the row.
    assert out[0]["exception_count"] == 7


def test_fraud_rate_trend_still_reports_a_real_zero():
    """A month WITH invoices and no exceptions genuinely has a 0% rate — the
    null state must not swallow it."""
    out = compute_fraud_rate_trend(
        [{"month": "2026-05", "invoice_count": 40, "exception_count": 0, "by_type": {}}]
    )
    assert out[0]["rate_pct"] == Decimal("0.0")
    assert out[0]["insufficient_data"] is False


@pytest.mark.asyncio
async def test_cfo_fraud_rate_trend_serialises_null_for_an_empty_month(realdb):
    """End-to-end: an empty tenant has six invoice-free months, so every
    `rate_pct` on the wire is `null` with `insufficient_data` true — never a
    JSON `0.0` a chart would draw as a clean bar."""
    async with realdb.client(key=TENANT, role="cfo") as c:
        resp = await c.get("/api/analytics/cfo?period_days=365")
    assert resp.status_code == 200, resp.text
    trend = resp.json()["fraud_rate_trend"]
    assert trend, "the CFO surface always returns six monthly buckets"
    for row in trend:
        assert row["invoice_count"] == 0
        assert row["rate_pct"] is None
        assert row["insufficient_data"] is True


# ---------------------------------------------------------------------------
# 2. forecast variance — currency, and the malformed-month 500
# ---------------------------------------------------------------------------


async def _default_entity_id(s):
    return (await s.execute(select(Entity.id).where(Entity.is_default))).scalar_one()


async def _seed_mixed_currency_payments(realdb) -> None:
    """One USD payment and one EUR payment carrying the rate-locked
    home-currency debit leg, both completed inside the current month."""
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    today = utc_today()
    # Clamp into the current calendar month so the month bucket under test
    # catches both regardless of what day the suite runs.
    when = datetime.combine(
        max(today - timedelta(days=1), today.replace(day=1)), datetime.min.time()
    ).replace(tzinfo=UTC)
    async with mk() as s:
        ent = await _default_entity_id(s)
        usd = Invoice(
            organization_id=org_id,
            entity_id=ent,
            invoice_number=f"FV-USD-{uuid.uuid4().hex[:6]}",
            vendor_name="Forecast Variance Co",
            amount=_USD_FACE,
            currency="USD",
            status=InvoiceStatus.paid,
            invoice_date=today,
        )
        eur = Invoice(
            organization_id=org_id,
            entity_id=ent,
            invoice_number=f"FV-EUR-{uuid.uuid4().hex[:6]}",
            vendor_name="Forecast Variance Co",
            amount=_EUR_FACE,
            currency="EUR",
            status=InvoiceStatus.paid,
            invoice_date=today,
        )
        s.add_all([usd, eur])
        await s.flush()
        s.add(
            Payment(
                entity_id=ent,
                invoice_id=usd.id,
                amount=_USD_FACE,
                method="ach",
                provider="mock",
                status="completed",
                completed_at=when,
            )
        )
        s.add(
            Payment(
                entity_id=ent,
                invoice_id=eur.id,
                amount=_EUR_FACE,
                source_amount=_EUR_AS_USD,
                source_currency="USD",
                method="wire",
                provider="mock",
                status="completed",
                completed_at=when,
            )
        )
        await s.commit()


@pytest.mark.asyncio
async def test_forecast_variance_actual_is_resolved_into_the_reporting_currency(realdb):
    """`Payment.amount` is denominated in the INVOICE's currency, so the old
    `SUM(Payment.amount)` reported 2000.00 for 1000 USD + 1000 EUR and
    measured the CFO's USD forecast against it.

    The converted actual is 1000.00 + 1086.96 = 2086.96, and the variance
    moves with it. Pre-fix `actual` is `2000.00` — this assertion fails.
    """
    await _seed_mixed_currency_payments(realdb)
    month = utc_today().strftime("%Y-%m")
    async with realdb.client(key=TENANT, role="cfo") as c:
        resp = await c.post(
            "/api/analytics/forecast_variance",
            json={"months": [{"month": month, "forecast": "2000.00"}]},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["reporting_currency"] == "USD"
    row = body["rows"][0]
    assert Decimal(row["actual"]) == _USD_FACE + _EUR_AS_USD
    assert Decimal(row["variance"]) == Decimal("86.96")
    # Nothing was excluded, so the figure is not a floor.
    assert row["unconverted_count"] == 0


@pytest.mark.asyncio
async def test_forecast_variance_excludes_and_discloses_an_unexpressible_payment(realdb):
    """A EUR payment with no home-currency leg cannot be expressed in USD at
    all. It is EXCLUDED rather than added at face value (a variance is acted
    on, so it must read as the floor it is) — and the exclusion is counted on
    the wire, not swallowed."""
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    today = utc_today()
    when = datetime.combine(
        max(today - timedelta(days=1), today.replace(day=1)), datetime.min.time()
    ).replace(tzinfo=UTC)
    async with mk() as s:
        ent = await _default_entity_id(s)
        eur = Invoice(
            organization_id=org_id,
            entity_id=ent,
            invoice_number=f"FV-NOFX-{uuid.uuid4().hex[:6]}",
            vendor_name="Forecast Variance Co",
            amount=_EUR_FACE,
            currency="EUR",
            status=InvoiceStatus.paid,
            invoice_date=today,
        )
        s.add(eur)
        await s.flush()
        s.add(
            Payment(
                entity_id=ent,
                invoice_id=eur.id,
                amount=_EUR_FACE,
                method="wire",
                provider="mock",
                status="completed",
                completed_at=when,
            )
        )
        await s.commit()

    month = today.strftime("%Y-%m")
    async with realdb.client(key=TENANT, role="cfo") as c:
        resp = await c.post(
            "/api/analytics/forecast_variance",
            json={"months": [{"month": month, "forecast": "1000.00"}]},
        )
    assert resp.status_code == 200, resp.text
    row = resp.json()["rows"][0]
    assert Decimal(row["actual"]) == Decimal("0.00")
    assert row["unconverted_count"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("month", ["2026-13", "2026-00", "2026-99", "0000-01"])
async def test_forecast_variance_rejects_an_unparseable_month_with_422(realdb, month):
    """`"2026-13"` splits into two ints, so it sailed past the guarded parse
    and hit an unguarded `date(2026, 13, 1)` — a bare `ValueError` -> 500 on
    ordinary caller input. It is a validation failure: 422."""
    async with realdb.client(key=TENANT, role="cfo") as c:
        resp = await c.post(
            "/api/analytics/forecast_variance",
            json={"months": [{"month": month, "forecast": "100"}]},
        )
    assert resp.status_code == 422, f"{month} -> {resp.status_code}: {resp.text}"


@pytest.mark.asyncio
async def test_forecast_variance_still_rejects_a_wrong_shaped_month(realdb):
    """The pre-existing length/format guard keeps working, and now answers
    with the same 422 as its sibling rather than a different code for the same
    class of failure."""
    async with realdb.client(key=TENANT, role="cfo") as c:
        resp = await c.post(
            "/api/analytics/forecast_variance",
            json={"months": [{"month": "2026/07", "forecast": "100"}]},
        )
    assert resp.status_code == 422, resp.text
