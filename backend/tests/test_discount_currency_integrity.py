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
    OFFER_STATUS_EXPIRED,
    OFFER_STATUS_OFFERED,
    DiscountOffer,
)
from app.models.entity import Entity
from app.models.invoice import Invoice, InvoiceStatus
from app.models.organization import Organization
from app.services.currency_conversion import resolve_reporting_currency
from app.utils.dates import utc_today

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


# ---------------------------------------------------------------------------
# The partial-set contract in depth
#
# The response declares ONE currency code and carries THREE money figures.
# Pre-fix, `captured_amount` and `missed_amount` were unfiltered cross-currency
# SUMs while `projected_savings` beside them was filtered — so one response
# described two different populations at once, and the reader had no way to
# tell. These tests make that combination impossible: all three figures must
# describe the SAME single-currency population, and the counts must say
# exactly what was left out of each.
# ---------------------------------------------------------------------------

_TIERS_3PCT = [{"days": 5, "percent": "3.00"}]


async def _add_open_offer(mk, org_id, *, currency: str, base_amount: str = "1000.00") -> None:
    """An `offered` offer whose 5-day tier is still achievable and worthwhile:
    `valid_from` today, priced against an invoice due in 30 days, so the
    optimizer accelerates 25 days at 3% (APR ~45%, well above the 8% default
    cost of capital) and selects it under the dashboard's unconstrained budget.
    Savings are therefore a deterministic 3% of `base_amount`."""
    today = utc_today()
    async with mk() as s:
        entity_id = await _default_entity_id(s)
        inv = Invoice(
            organization_id=org_id,
            entity_id=entity_id,
            invoice_number=f"INV-{uuid.uuid4().hex[:8]}",
            vendor_name="Globex",
            amount=Decimal(base_amount),
            currency=currency,
            due_date=today + timedelta(days=30),
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
                status=OFFER_STATUS_OFFERED,
                currency=currency,
                base_amount=Decimal(base_amount),
                tiers=list(_TIERS_3PCT),
                valid_from=today,
                valid_until=today + timedelta(days=60),
            )
        )
        await s.commit()


@pytest.mark.asyncio
async def test_all_three_money_figures_describe_the_same_population(realdb):
    """One captured, one missed and one open offer in each of three
    currencies. Every money figure must describe only the USD third of that
    set, and each of the three disclosure counts must report the other two."""
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    await _set_reporting_currency(realdb, org_id, "USD")
    for cur in ("USD", "EUR", "GBP"):
        await _add_offer(
            mk, org_id, status=OFFER_STATUS_CAPTURED, currency=cur, captured_amount="111.00"
        )
        # 10% of 1000 = 100 would have been captured.
        await _add_offer(mk, org_id, status=OFFER_STATUS_DECLINED, currency=cur)
        await _add_open_offer(mk, org_id, currency=cur)

    async with realdb.client(key="a", role="cfo") as c:
        body = (await c.get("/api/discounts/dashboard")).json()

    assert body["currency"] == "USD"
    # Realised: exactly the USD rows.
    assert Decimal(str(body["captured_amount"])) == Decimal("111.00")
    assert Decimal(str(body["missed_amount"])) == Decimal("100.00")
    # Projected: 3% of the one USD open offer, and nothing from the other two.
    assert Decimal(str(body["projected_savings"])) == Decimal("30.00")
    # …and every figure says what it left out, with the same answer.
    assert body["excluded_captured_count"] == 2
    assert body["excluded_missed_count"] == 2
    assert body["unconvertible_offer_count"] == 2
    # The counts beside the money describe that same USD population.
    assert body["captured_count"] == 1
    assert body["missed_count"] == 1
    # `open_offer_count` is deliberately a whole-set count of open offers (it
    # sizes the queue, not a money figure) — the currency caveat above it is
    # what reconciles the two.
    assert body["open_offer_count"] == 3


@pytest.mark.asyncio
async def test_capture_rate_is_derived_from_the_filtered_population(realdb):
    """`capture_rate_pct` is `captured / (captured + missed)`. Both terms are
    now single-currency, so the ratio describes the population the money
    figures do. Pre-fix both counts were whole-set, so the rate silently
    answered a different question than the two amounts beside it."""
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    await _set_reporting_currency(realdb, org_id, "USD")
    await _add_offer(
        mk, org_id, status=OFFER_STATUS_CAPTURED, currency="USD", captured_amount="50.00"
    )
    await _add_offer(mk, org_id, status=OFFER_STATUS_DECLINED, currency="USD")
    # Four foreign captured rows would drag a whole-set rate to 5/6 = 83.33%.
    for _ in range(4):
        await _add_offer(
            mk, org_id, status=OFFER_STATUS_CAPTURED, currency="EUR", captured_amount="50.00"
        )

    async with realdb.client(key="a", role="cfo") as c:
        body = (await c.get("/api/discounts/dashboard")).json()

    assert Decimal(str(body["capture_rate_pct"])) == Decimal("50.00")
    assert body["captured_count"] == 1
    assert body["missed_count"] == 1
    assert body["excluded_captured_count"] == 4
    assert body["excluded_missed_count"] == 0


@pytest.mark.asyncio
async def test_an_exclusion_in_one_bucket_is_not_reported_in_the_other(realdb):
    """The two counts are independent: a foreign CAPTURED row must not make
    the missed figure look partial, and vice versa. A single shared count
    would read as "both figures are incomplete" on every mixed tenant."""
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    await _set_reporting_currency(realdb, org_id, "USD")
    await _add_offer(
        mk, org_id, status=OFFER_STATUS_CAPTURED, currency="EUR", captured_amount="10.00"
    )
    await _add_offer(mk, org_id, status=OFFER_STATUS_DECLINED, currency="USD")

    async with realdb.client(key="a", role="cfo") as c:
        body = (await c.get("/api/discounts/dashboard")).json()

    assert body["excluded_captured_count"] == 1
    assert body["excluded_missed_count"] == 0
    assert Decimal(str(body["missed_amount"])) == Decimal("100.00")
    assert Decimal(str(body["captured_amount"])) == Decimal("0")

    # Now the mirror image, in the other bucket only.
    await _add_offer(
        mk, org_id, status=OFFER_STATUS_CAPTURED, currency="USD", captured_amount="25.00"
    )
    await _add_offer(mk, org_id, status=OFFER_STATUS_EXPIRED, currency="GBP")

    async with realdb.client(key="a", role="cfo") as c:
        body = (await c.get("/api/discounts/dashboard")).json()

    assert body["excluded_captured_count"] == 1
    assert body["excluded_missed_count"] == 1
    assert Decimal(str(body["captured_amount"])) == Decimal("25.00")


@pytest.mark.asyncio
async def test_an_expired_offer_counts_as_missed_like_a_declined_one(realdb):
    """The missed bucket is declined + expired, and the currency filter must
    apply to both halves — not just the one the earlier tests happen to use."""
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    await _set_reporting_currency(realdb, org_id, "USD")
    await _add_offer(mk, org_id, status=OFFER_STATUS_EXPIRED, currency="USD")
    await _add_offer(mk, org_id, status=OFFER_STATUS_EXPIRED, currency="EUR", base_amount="7000.00")

    async with realdb.client(key="a", role="cfo") as c:
        body = (await c.get("/api/discounts/dashboard")).json()

    assert body["missed_count"] == 1
    assert Decimal(str(body["missed_amount"])) == Decimal("100.00")
    assert body["excluded_missed_count"] == 1


@pytest.mark.asyncio
async def test_a_captured_offer_with_no_captured_amount_is_counted_not_dropped(realdb):
    """`captured_amount` is nullable (captured through a path that never
    stamped the figure). The row must still be counted — otherwise the capture
    rate quietly improves — while contributing nothing to the money."""
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    await _set_reporting_currency(realdb, org_id, "USD")
    await _add_offer(mk, org_id, status=OFFER_STATUS_CAPTURED, currency="USD")
    await _add_offer(
        mk, org_id, status=OFFER_STATUS_CAPTURED, currency="USD", captured_amount="40.00"
    )

    async with realdb.client(key="a", role="cfo") as c:
        body = (await c.get("/api/discounts/dashboard")).json()

    assert body["captured_count"] == 2
    assert Decimal(str(body["captured_amount"])) == Decimal("40.00")
    assert body["excluded_captured_count"] == 0


@pytest.mark.asyncio
async def test_the_exclusion_counts_are_entity_scoped(realdb):
    """The disclosure counts run through the SAME `apply_entity_scope` wrapper
    as the figures they qualify. A subsidiary's foreign-currency offers must
    not make another subsidiary's complete figure look partial."""
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    await _set_reporting_currency(realdb, org_id, "USD")
    await _add_offer(
        mk, org_id, status=OFFER_STATUS_CAPTURED, currency="USD", captured_amount="60.00"
    )

    async with mk() as s:
        default_entity = await _default_entity_id(s)
        sub = Entity(
            organization_id=org_id,
            name="EU Subsidiary",
            slug=f"eu-{uuid.uuid4().hex[:6]}",
            is_default=False,
        )
        s.add(sub)
        await s.flush()
        sub_id = sub.id
        s.add(
            DiscountOffer(
                organization_id=org_id,
                entity_id=sub_id,
                scope=OFFER_SCOPE_INVOICE,
                status=OFFER_STATUS_CAPTURED,
                currency="EUR",
                base_amount=Decimal("1000.00"),
                tiers=list(_TIERS_3PCT),
                captured_amount=Decimal("500.00"),
                valid_until=utc_today() + timedelta(days=60),
            )
        )
        await s.commit()

    async with realdb.client(key="a", role="cfo") as c:
        scoped = (
            await c.get("/api/discounts/dashboard", headers={"X-Entity-ID": str(default_entity)})
        ).json()
        consolidated = (await c.get("/api/discounts/dashboard")).json()

    # Scoped to the default entity: the EUR row is out of scope entirely, so
    # nothing is excluded and the figure is whole.
    assert Decimal(str(scoped["captured_amount"])) == Decimal("60.00")
    assert scoped["captured_count"] == 1
    assert scoped["excluded_captured_count"] == 0
    # Consolidated: the EUR row is in scope and IS disclosed as excluded.
    assert Decimal(str(consolidated["captured_amount"])) == Decimal("60.00")
    assert consolidated["excluded_captured_count"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["admin", "ap_manager", "ap_clerk", "cfo"])
async def test_every_read_role_may_see_the_dashboard(realdb, role):
    """All four roles read the discounting surfaces (`_READ_ROLES`). The
    currency fix must not have narrowed that — a clerk seeing a 403 where the
    nav offers a link is the shape of bug this repo already fixed once for
    Decline."""
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    await _set_reporting_currency(realdb, org_id, "EUR")
    await _add_offer(
        mk, org_id, status=OFFER_STATUS_CAPTURED, currency="EUR", captured_amount="12.00"
    )

    async with realdb.client(key="a", role=role) as c:
        resp = await c.get("/api/discounts/dashboard")

    assert resp.status_code == 200
    assert resp.json()["currency"] == "EUR"
    assert Decimal(str(resp.json()["captured_amount"])) == Decimal("12.00")


@pytest.mark.asyncio
async def test_the_dashboard_is_not_public(realdb):
    """Auth before everything — the rollup names a tenant's realised savings."""
    async with realdb.client(key="a", role=None) as c:
        assert (await c.get("/api/discounts/dashboard")).status_code == 401


@pytest.mark.asyncio
async def test_one_tenants_offers_never_reach_another_tenants_dashboard(realdb):
    """Tenant isolation at the data layer: tenant B's rollup — including its
    exclusion counts — must not see tenant A's rows."""
    mk = realdb.sessionmaker("a")
    org_a = realdb.info("a").org_id
    await _set_reporting_currency(realdb, org_a, "USD")
    await _add_offer(
        mk, org_a, status=OFFER_STATUS_CAPTURED, currency="USD", captured_amount="99.00"
    )
    await _add_offer(mk, org_a, status=OFFER_STATUS_CAPTURED, currency="EUR")

    async with realdb.client(key="b", role="cfo") as c:
        body = (await c.get("/api/discounts/dashboard")).json()

    assert body["captured_count"] == 0
    assert Decimal(str(body["captured_amount"])) == Decimal("0")
    assert body["excluded_captured_count"] == 0
    assert body["excluded_missed_count"] == 0
