"""Money on `/api/analytics/*` is an EXACT decimal string, and nothing else is.

The project invariant is that money never crosses the API boundary as a float,
because a binary float can't hold an exact cent value — and this module is
where that invariant was violated in ~45 places at once, feeding a shipped web
dashboard and a shipped Flutter screen. `docs/decisions.md` §32 records why
`/drill/dpo` was corrected alone and the rest deferred; this is the rest.

The guard is **structural, not per-field**, because a per-field assertion only
pins the fields someone remembered to list. Instead every response is walked
recursively and every JSON *number* it contains must appear in
``NUMERIC_FIELDS`` — the declared roster of things that are genuinely not
money:

  - **day counts** (`dpo`, `cash_conversion_cycle`, `weighted_avg_pay_date_days`)
  - **percentages** (`*_share_pct`, `rate_pct`, `yield_pct`, `variance_pct`)
  - **counts** and request echoes (`period_days`, `horizon_days`, …)

So a NEW money field added as a float fails here until it is either serialised
through ``analytics._money`` or consciously declared non-money — which is the
whole point. Converting a day count or a percentage to a string would be a bug
wearing compliance's clothes, so the roster is the other half of the contract,
not an escape hatch.

Every declared money field is additionally asserted to parse as a Decimal, so
a string that isn't actually a number can't sneak through the type check.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from app.models.invoice import Invoice, InvoiceStatus

_TODAY = date.today()

# Every JSON number legitimately reachable in an /api/analytics/* response.
# A key NOT in here must serialise as an exact decimal string.
NUMERIC_FIELDS: frozenset[str] = frozenset(
    {
        # --- request echoes / window parameters -------------------------------
        "period_days",
        "horizon_days",
        "grace_days",
        "months",
        # --- day counts (NOT money) -------------------------------------------
        "dpo",
        "dpo_current",
        "cash_conversion_cycle",
        "weighted_avg_pay_date_days",
        # --- percentages (NOT money) ------------------------------------------
        "share_pct",
        "top_10_share_pct",
        "top_50_share_pct",
        "largest_vendor_share_pct",
        "rate_pct",
        "yield_pct",
        "variance_pct",
        # --- counts -----------------------------------------------------------
        "count",
        "total_count",
        "unconverted_count",
        "invoice_count",
        "exception_count",
        "open_exceptions",
    }
)


def _assert_numbers_are_declared(node, path: str = "$") -> None:
    """Walk a response body; every JSON number must be a declared non-money key.

    `bool` is excluded explicitly — it is an `int` subclass in Python, and
    `below_threshold` / `flagged` / `available` are flags, not figures.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}"
            if isinstance(value, bool):
                continue
            if isinstance(value, int | float):
                assert key in NUMERIC_FIELDS, (
                    f"{child} serialises as a JSON number ({value!r}). If it is money it must "
                    "go through `analytics._money` (exact decimal string); if it is genuinely "
                    "a day count / percentage / count, add it to NUMERIC_FIELDS."
                )
                continue
            _assert_numbers_are_declared(value, child)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            _assert_numbers_are_declared(value, f"{path}[{i}]")


def _assert_money_strings(node, *, keys: set[str], path: str = "$") -> set[str]:
    """Assert every occurrence of `keys` is a Decimal-parseable string (or null).

    Returns the set of keys actually seen, so a caller can prove the fields it
    cares about were present rather than silently absent from a thin fixture.
    """
    seen: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}"
            if key in keys:
                seen.add(key)
                if value is None:
                    continue
                assert isinstance(value, str), (
                    f"{child} is money and must serialise as an exact decimal string, got {value!r}"
                )
                try:
                    Decimal(value)
                except InvalidOperation as exc:  # pragma: no cover - failure path
                    raise AssertionError(f"{child} is not a decimal string: {value!r}") from exc
            seen |= _assert_money_strings(value, keys=keys, path=child)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            seen |= _assert_money_strings(value, keys=keys, path=f"{path}[{i}]")
    return seen


async def _seed(realdb, key: str = "a") -> None:
    """A minimal population that makes every CFO tile non-empty.

    An approved invoice inside the trailing window gives spend, an open AP
    balance, a concentration row and a forecast commitment; a completed
    payment against it gives the paid-in-period leg `avg_daily_outflow` and
    `working_capital_impact_5_days` are derived from.
    """
    from app.models.payment import Payment

    mk = realdb.sessionmaker(key)
    async with mk() as s:
        inv = Invoice(
            organization_id=realdb.info(key).org_id,
            invoice_number=f"MS-{uuid.uuid4().hex[:8]}",
            vendor_name="Acme Supplies",
            amount=Decimal("1234.56"),
            currency="USD",
            status=InvoiceStatus.approved.value,
            invoice_date=_TODAY - timedelta(days=5),
            due_date=_TODAY + timedelta(days=10),
        )
        s.add(inv)
        await s.flush()
        s.add(
            Payment(
                invoice_id=inv.id,
                amount=Decimal("500.25"),
                method="ach",
                status="completed",
                completed_at=datetime.combine(
                    _TODAY - timedelta(days=1), datetime.min.time()
                ).replace(tzinfo=UTC),
            )
        )
        await s.commit()


# ---------------------------------------------------------------------------
# /api/analytics/cfo — the aggregate dashboard
# ---------------------------------------------------------------------------

# Declared so a fixture that happens not to exercise a tile can't make the
# structural walk vacuously pass for that tile's money.
_CFO_MONEY_FIELDS = {
    "total_spend",
    "total_amount",
    "original_amount",
    "reporting_amount",
    "accounts_payable_balance",
    "open_po_amount",
    "received_amount",
    "unposted_invoice_amount",
    "total_accrual",
    "working_capital_impact_5_days",
    "avg_daily_outflow",
    "rebates_total",
    "annualised_rebates",
    "total_unrealized_gain_loss",
    "open_original_amount",
    "booked_reporting_amount",
    "current_reporting_amount",
    "unrealized_gain_loss",
}


async def test_cfo_money_is_exact_strings_and_day_counts_stay_numbers(realdb):
    await _seed(realdb)
    async with realdb.client(key="a", role="cfo") as c:
        resp = await c.get("/api/analytics/cfo?period_days=365")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    _assert_numbers_are_declared(body)
    seen = _assert_money_strings(body, keys=_CFO_MONEY_FIELDS)

    # The tiles the seeded population guarantees are present.
    for required in (
        "total_spend",
        "accounts_payable_balance",
        "total_accrual",
        "avg_daily_outflow",
        "working_capital_impact_5_days",
        "rebates_total",
    ):
        assert required in seen, f"{required} missing from the response"

    # The seeded figures survive exactly — the whole reason for the change.
    assert Decimal(body["total_spend"]) == Decimal("1234.56")
    assert Decimal(body["accounts_payable_balance"]) == Decimal("1234.56")
    assert Decimal(body["supplier_concentration"]["total_spend"]) == Decimal("1234.56")

    # Day counts and percentages are numbers, not strings.
    assert isinstance(body["dpo_current"], int | float)
    assert body["cash_conversion_cycle"] is None or isinstance(
        body["cash_conversion_cycle"], int | float
    )
    assert isinstance(body["supplier_concentration"]["top_10_share_pct"], int | float)
    assert isinstance(body["rebate_yield"]["yield_pct"], int | float)


async def test_cfo_zero_population_still_serialises_money_as_strings(realdb):
    """An empty tenant must not fall back to numeric zeros.

    `0` and `"0"` both read as "nothing here", so a zero-population response is
    exactly where a float would go unnoticed for months.
    """
    async with realdb.client(key="a", role="cfo") as c:
        resp = await c.get("/api/analytics/cfo")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    _assert_numbers_are_declared(body)
    _assert_money_strings(body, keys=_CFO_MONEY_FIELDS)
    assert body["total_spend"] == "0"
    # The unrealized-FX fallback zero rides the same rule.
    assert isinstance(body["unrealized_fx"]["total_unrealized_gain_loss"], str)


# ---------------------------------------------------------------------------
# Predictive cash-flow trio
# ---------------------------------------------------------------------------

_CASHFLOW_MONEY_FIELDS = {
    "scheduled_amount",
    "committed_amount",
    "pending_amount",
    "discount_eligible_amount",
    "total_outflow",
    "total_discount_captured",
    "opening_balance",
    "threshold",
    "opening",
    "outflow",
    "inflow",
    "closing",
    "shortfall",
}


async def test_cashflow_endpoints_money_is_exact_strings(realdb):
    await _seed(realdb)
    async with realdb.client(key="a", role="cfo") as c:
        for url in (
            "/api/analytics/cashflow_forecast?granularity=month",
            "/api/analytics/cashflow_whatif?granularity=month",
            "/api/analytics/cash_position?granularity=month&opening_balance=5000",
        ):
            resp = await c.get(url)
            assert resp.status_code == 200, f"{url}: {resp.text}"
            body = resp.json()
            _assert_numbers_are_declared(body, path=url)
            _assert_money_strings(body, keys=_CASHFLOW_MONEY_FIELDS, path=url)


async def test_cash_position_null_threshold_stays_null(realdb):
    """No threshold set is `null`, never `"0"` — which would read as
    'alert on any positive balance'."""
    async with realdb.client(key="a", role="cfo") as c:
        resp = await c.get("/api/analytics/cash_position")
    assert resp.status_code == 200, resp.text
    assert resp.json()["threshold"] is None
