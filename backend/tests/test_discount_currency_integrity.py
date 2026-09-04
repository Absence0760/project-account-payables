"""Currency integrity on the dynamic-discounting surfaces.

Two defects from the round-14 money-path hunt, both recorded in
`docs/followups.md` as unverified leads and both confirmed here:

  * `api/discounts._org_currency` read `settings.reporting_currency` alone and
    fell back to a hardcoded "USD", diverging from the canonical
    `resolve_reporting_currency` chain. An org that set `payments.home_currency`
    but no explicit reporting currency had every discount figure stamped USD.
  * `GET /api/discounts/dashboard` summed `captured_amount` and `missed_amount`
    across every currency and stamped the total with the reporting currency —
    while `projected_savings` in the SAME response was correctly filtered. One
    response carried one currency-correct figure and two that were not.

A cross-currency SUM presented under one code is not an approximation of the
truth; it is a different quantity, and it changes silently whenever the
currency mix changes.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.api.discounts import _org_currency
from app.models.discount import (
    OFFER_SCOPE_INVOICE,
    OFFER_STATUS_CAPTURED,
    OFFER_STATUS_DECLINED,
    DiscountOffer,
)
from app.models.entity import Entity
from app.models.invoice import Invoice, InvoiceStatus
from app.models.organization import Organization
from app.services.currency_conversion import resolve_reporting_currency

# ---------------------------------------------------------------------------
# _org_currency delegates to the canonical resolver
# ---------------------------------------------------------------------------


def _org(settings: dict | None):
    class _O:
        pass

    o = _O()
    o.settings = settings
    return o


@pytest.mark.parametrize(
    "settings",
    [
        {"reporting_currency": "EUR"},
        {"payments": {"home_currency": "GBP"}},
        {"invoice_defaults": {"currency": "CAD"}},
        # Precedence: an explicit reporting currency beats the others.
        {"reporting_currency": "JPY", "payments": {"home_currency": "GBP"}},
        # Falls through the whole chain to the platform default.
        {},
        None,
        # A misconfigured blob degrades rather than raising.
        {"reporting_currency": ""},
        {"reporting_currency": "  eur  "},
    ],
)
def test_org_currency_matches_the_canonical_resolver(settings):
    """The discounts helper must agree with `resolve_reporting_currency` on
    every input, not just the one key it used to read."""
    assert _org_currency(_org(settings)) == resolve_reporting_currency(settings)


def test_org_currency_honours_home_currency_without_an_explicit_reporting_currency():
    """The exact divergence the lead described: this returned "USD" before."""
    assert _org_currency(_org({"payments": {"home_currency": "EUR"}})) == "EUR"


def test_org_currency_is_not_hardcoded_to_usd():
    """The old fallback was a literal "USD" that ignored the platform default."""
    assert _org_currency(_org({"invoice_defaults": {"currency": "SEK"}})) == "SEK"


# ---------------------------------------------------------------------------
# dashboard — realised figures are single-currency
# ---------------------------------------------------------------------------

_FUTURE = (date.today() + timedelta(days=60)).isoformat()


async def _default_entity_id(s):
    return (
        await s.execute(select(Entity.id).where(Entity.is_default.is_(True)).limit(1))
    ).scalar_one()


async def _set_reporting_currency(realdb, org_id, code: str) -> None:
    async with realdb.control_sessionmaker()() as s:
        org = (await s.execute(select(Organization).where(Organization.id == org_id))).scalar_one()
        settings = dict(org.settings or {})
        settings["reporting_currency"] = code
        org.settings = settings
        await s.commit()


async def _add_offer(
    mk,
    org_id,
    *,
    status: str,
    currency: str,
    base_amount: str = "1000.00",
    captured_amount: str | None = None,
) -> None:
    """One offer in a terminal state, denominated in `currency`.

    Written directly rather than through the API so the currency can be set
    per-row — the create endpoint inherits the invoice's currency, and the
    point here is a tenant holding offers in more than one.
    """
    async with mk() as s:
        entity_id = await _default_entity_id(s)
        inv = Invoice(
            organization_id=org_id,
            entity_id=entity_id,
            invoice_number=f"INV-{uuid.uuid4().hex[:8]}",
            vendor_name="Globex",
            amount=Decimal(base_amount),
            currency=currency,
            due_date=date.today() + timedelta(days=30),
            status=InvoiceStatus.approved,
        )
        s.add(inv)
        await s.flush()
        s.add(
            DiscountOffer(
                organization_id=org_id,
                entity_id=entity_id,
                invoice_id=inv.id,
                scope=OFFER_SCOPE_INVOICE,
                status=status,
                currency=currency,
                base_amount=Decimal(base_amount),
                tiers=[{"days": 5, "percent": "10.00"}],
                captured_amount=Decimal(captured_amount) if captured_amount else None,
                valid_until=date.today() + timedelta(days=60),
            )
        )
        await s.commit()


@pytest.mark.asyncio
async def test_captured_amount_excludes_other_currencies(realdb):
    """USD 100 captured + EUR 500 captured must report 100 under USD — not 600,
    which is neither a USD figure nor a EUR one."""
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    await _set_reporting_currency(realdb, org_id, "USD")
    await _add_offer(
        mk, org_id, status=OFFER_STATUS_CAPTURED, currency="USD", captured_amount="100.00"
    )
    await _add_offer(
        mk, org_id, status=OFFER_STATUS_CAPTURED, currency="EUR", captured_amount="500.00"
    )

    async with realdb.client(key="a", role="cfo") as c:
        body = (await c.get("/api/discounts/dashboard")).json()

    assert body["currency"] == "USD"
    assert Decimal(str(body["captured_amount"])) == Decimal("100.00")
    assert body["captured_count"] == 1
    assert body["excluded_captured_count"] == 1


@pytest.mark.asyncio
async def test_missed_amount_excludes_other_currencies(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    await _set_reporting_currency(realdb, org_id, "USD")
    # 10% of 1000 = 100 missed, in USD.
    await _add_offer(mk, org_id, status=OFFER_STATUS_DECLINED, currency="USD")
    # 10% of 9000 = 900 missed, in EUR — must not reach the USD figure.
    await _add_offer(
        mk, org_id, status=OFFER_STATUS_DECLINED, currency="EUR", base_amount="9000.00"
    )

    async with realdb.client(key="a", role="cfo") as c:
        body = (await c.get("/api/discounts/dashboard")).json()

    assert Decimal(str(body["missed_amount"])) == Decimal("100.00")
    assert body["missed_count"] == 1
    assert body["excluded_missed_count"] == 1


@pytest.mark.asyncio
async def test_dashboard_follows_the_orgs_actual_reporting_currency(realdb):
    """With the org on EUR, the EUR rows are the ones that count — proving the
    filter follows the resolver rather than a hardcoded USD."""
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    await _set_reporting_currency(realdb, org_id, "EUR")
    await _add_offer(
        mk, org_id, status=OFFER_STATUS_CAPTURED, currency="USD", captured_amount="100.00"
    )
    await _add_offer(
        mk, org_id, status=OFFER_STATUS_CAPTURED, currency="EUR", captured_amount="500.00"
    )

    async with realdb.client(key="a", role="cfo") as c:
        body = (await c.get("/api/discounts/dashboard")).json()

    assert body["currency"] == "EUR"
    assert Decimal(str(body["captured_amount"])) == Decimal("500.00")
    assert body["excluded_captured_count"] == 1


@pytest.mark.asyncio
async def test_single_currency_tenant_reports_nothing_excluded(realdb):
    """The common case must be unchanged: no exclusions, and the figures are
    the same ones the old bare SUM produced."""
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    await _set_reporting_currency(realdb, org_id, "USD")
    await _add_offer(
        mk, org_id, status=OFFER_STATUS_CAPTURED, currency="USD", captured_amount="250.00"
    )
    await _add_offer(mk, org_id, status=OFFER_STATUS_DECLINED, currency="USD")

    async with realdb.client(key="a", role="cfo") as c:
        body = (await c.get("/api/discounts/dashboard")).json()

    assert body["excluded_captured_count"] == 0
    assert body["excluded_missed_count"] == 0
    assert Decimal(str(body["captured_amount"])) == Decimal("250.00")


@pytest.mark.asyncio
async def test_currency_matching_is_case_insensitive(realdb):
    """`DiscountOffer.currency` is free-form varchar(3); a lowercased row is
    the same currency and must not be excluded as if it were foreign."""
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    await _set_reporting_currency(realdb, org_id, "USD")
    await _add_offer(
        mk, org_id, status=OFFER_STATUS_CAPTURED, currency="usd", captured_amount="75.00"
    )

    async with realdb.client(key="a", role="cfo") as c:
        body = (await c.get("/api/discounts/dashboard")).json()

    assert Decimal(str(body["captured_amount"])) == Decimal("75.00")
    assert body["excluded_captured_count"] == 0


@pytest.mark.asyncio
async def test_every_money_field_in_the_response_shares_one_currency(realdb):
    """The invariant behind all of the above: the response declares ONE code,
    so no money field in it may aggregate a row denominated differently."""
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    await _set_reporting_currency(realdb, org_id, "USD")
    for cur in ("USD", "EUR", "GBP", "JPY"):
        await _add_offer(
            mk, org_id, status=OFFER_STATUS_CAPTURED, currency=cur, captured_amount="10.00"
        )
        await _add_offer(mk, org_id, status=OFFER_STATUS_DECLINED, currency=cur)

    async with realdb.client(key="a", role="cfo") as c:
        body = (await c.get("/api/discounts/dashboard")).json()

    # One captured + one missed row are USD; the other three of each are not.
    assert Decimal(str(body["captured_amount"])) == Decimal("10.00")
    assert body["excluded_captured_count"] == 3
    assert body["excluded_missed_count"] == 3
    # And the counts beside the money describe the same population.
    assert body["captured_count"] == 1
    assert body["missed_count"] == 1
