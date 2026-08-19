"""Round-11 follow-ups on the cash-flow surfaces.

Four findings the round-10 money-path hunt confirmed by reading and left open,
all in the same family — a figure that is wrong, or a caveat nobody can see.

1. **`discount_eligible_amount` counted elapsed discount windows.** The
   commitment rows it consumes are bounded on their DUE date only, so an
   in-horizon invoice on 2/10-net-60 terms routinely arrives with a
   `discount_date` that passed weeks ago. The `early` what-if scenario was
   fixed for exactly this; the bucket total was not, so the forecast still
   reported savings nobody can take.

2. **The plan artifact dropped `unconverted_count`.** `bucket_outflows`
   computes it, `/analytics/cash_position` and the copilot's other tools
   surface it — but the ONE artifact a user can enact (draft-run,
   capture-discounts) carried none of it.

3. **The shortfall alert dropped it too.** That sweep emails finance leaders
   "your cash runs out in period X". A single unconverted ¥10,000,000 invoice
   manufactures that shortfall on a USD curve.

4. **`_coerce_decimal` opted an org out of alerting in silence.** A malformed
   or NaN `min_balance_threshold` returns `None`, and `None` is the "org did
   not opt in" signal — so a corrupt settings blob unsubscribes an org from the
   alert it configured, forever, with nothing logged.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal

from app.services.analytics import bucket_outflows, compute_cash_position
from app.services.cash_flow_alerts import project_shortfall
from app.services.cash_flow_plan import assemble_plan
from app.services.cashflow import resolve_cash_thresholds
from app.services.discount_optimizer import OptimizationResult
from app.services.notification_templates import render_cash_shortfall


def _commit(due, amount, **extra):
    row = {
        "due_date": due,
        "amount": Decimal(amount),
        "committed": True,
        "discount_date": None,
        "discount_percent": None,
    }
    row.update(extra)
    return row


# --------------------------------------------------------------------------- #
# 1. elapsed discount windows
# --------------------------------------------------------------------------- #


def test_discount_eligible_amount_ignores_an_elapsed_window():
    rows = [
        _commit(
            date(2026, 6, 1),
            "1000",
            discount_date=date(2026, 5, 25),
            discount_percent=Decimal("2"),
        )
    ]
    still_open = bucket_outflows(rows, granularity="week", today=date(2026, 5, 20))
    elapsed = bucket_outflows(rows, granularity="week", today=date(2026, 5, 26))

    assert still_open[0]["discount_eligible_amount"] == Decimal("1000.00")
    assert elapsed[0]["discount_eligible_amount"] == Decimal("0")
    # The outflow itself is unaffected — the invoice is still due, just not at
    # a discount.
    assert elapsed[0]["scheduled_amount"] == Decimal("1000.00")


def test_discount_eligible_amount_counts_the_deadline_day_itself():
    rows = [
        _commit(
            date(2026, 6, 1),
            "500",
            discount_date=date(2026, 5, 25),
            discount_percent=Decimal("2"),
        )
    ]
    out = bucket_outflows(rows, granularity="week", today=date(2026, 5, 25))
    assert out[0]["discount_eligible_amount"] == Decimal("500.00")


# --------------------------------------------------------------------------- #
# 2. the plan artifact carries the caveat
# --------------------------------------------------------------------------- #


def _empty_optimizer_result() -> OptimizationResult:
    return OptimizationResult(
        cost_of_capital_pct=Decimal("8.0"),
        total_savings_available=Decimal("0"),
        total_savings_selected=Decimal("0"),
        total_outlay_selected=Decimal("0"),
        recommendations=[],
    )


def test_plan_periods_and_artifact_carry_the_unconverted_count():
    rows = [
        {**_commit(date(2026, 6, 10), "10000000"), "invoice_id": "a", "unconverted": True},
        {**_commit(date(2026, 6, 11), "1000"), "invoice_id": "b", "unconverted": False},
    ]
    plan = assemble_plan(
        rows,
        optimizer_result=_empty_optimizer_result(),
        opening_balance=Decimal("250000"),
        min_balance_threshold=Decimal("50000"),
        granularity="week",
        horizon_days=90,
        today=date(2026, 6, 1),
    )
    assert plan.unconverted_count == 1
    assert sum(p.unconverted_count for p in plan.periods) == 1
    # The curve does flag a shortfall — which is exactly why the caveat has to
    # travel with it.
    assert plan.first_shortfall_period is not None


def test_plan_reports_zero_when_everything_converts():
    rows = [{**_commit(date(2026, 6, 10), "1000"), "invoice_id": "a", "unconverted": False}]
    plan = assemble_plan(
        rows,
        optimizer_result=_empty_optimizer_result(),
        opening_balance=Decimal("250000"),
        min_balance_threshold=None,
        granularity="week",
        horizon_days=90,
        today=date(2026, 6, 1),
    )
    assert plan.unconverted_count == 0


# --------------------------------------------------------------------------- #
# 3. the shortfall alert says so
# --------------------------------------------------------------------------- #


def test_shortfall_projection_carries_the_unconverted_count():
    periods = bucket_outflows(
        [{**_commit(date(2026, 6, 10), "10000000"), "unconverted": True}],
        granularity="week",
        today=date(2026, 6, 1),
    )
    position = compute_cash_position(
        Decimal("250000"), periods, min_balance_threshold=Decimal("100000")
    )
    breaches = [p for p in position if p["below_threshold"]]
    breaches = [
        {
            "period": p["period"],
            "closing": p["closing"],
            "shortfall": Decimal("100000") - p["closing"],
        }
        for p in breaches
    ]
    projection = project_shortfall(
        breaches,
        threshold=Decimal("100000"),
        currency="USD",
        unconverted_count=sum(int(b.get("unconverted_count", 0) or 0) for b in periods),
    )
    assert projection.unconverted_count == 1


def test_shortfall_email_states_the_caveat():
    rendered = render_cash_shortfall(
        period="2026-06-08",
        closing=Decimal("-9750000.00"),
        threshold=Decimal("100000.00"),
        shortfall=Decimal("9850000.00"),
        currency="USD",
        breach_count=3,
        unconverted_count=1,
    )
    assert "1 commitment(s) could not be converted" in rendered.body_text
    # PII-free: no vendor, no invoice number — counts and org-level figures only.
    assert "@" not in rendered.body_text


def test_shortfall_email_stays_clean_when_everything_converts():
    rendered = render_cash_shortfall(
        period="2026-06-08",
        closing=Decimal("50000.00"),
        threshold=Decimal("100000.00"),
        shortfall=Decimal("50000.00"),
        currency="USD",
        breach_count=1,
    )
    assert "could not be converted" not in rendered.body_text


# --------------------------------------------------------------------------- #
# 4. a malformed threshold is logged, and NaN is refused
# --------------------------------------------------------------------------- #


def test_malformed_threshold_is_logged_not_silently_dropped(caplog):
    with caplog.at_level(logging.WARNING):
        thresholds = resolve_cash_thresholds(
            {"cashflow": {"min_balance_threshold": "not-a-number"}}
        )
    assert thresholds.min_balance_threshold is None
    assert any("min_balance_threshold" in r.message for r in caplog.records)
    # The offending VALUE never reaches the log sink — settings are operator
    # data and the field name is enough to find it.
    assert all("not-a-number" not in r.getMessage() for r in caplog.records)


def test_nan_threshold_is_refused():
    """`Decimal(str("nan"))` parses. Every comparison against it is False, so
    the org looks configured while being just as opted out as a None."""
    thresholds = resolve_cash_thresholds({"cashflow": {"min_balance_threshold": "nan"}})
    assert thresholds.min_balance_threshold is None


def test_infinite_threshold_is_refused():
    thresholds = resolve_cash_thresholds({"cashflow": {"min_balance_threshold": "Infinity"}})
    assert thresholds.min_balance_threshold is None


def test_a_real_threshold_still_parses():
    thresholds = resolve_cash_thresholds({"cashflow": {"min_balance_threshold": "100000.50"}})
    assert thresholds.min_balance_threshold == Decimal("100000.50")
