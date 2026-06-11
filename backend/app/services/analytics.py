"""Analytics — pure computation functions for CFO + operational
metrics. Caller hands in the raw row sets pulled from the DB; the
functions return Decimal / dict results that the API layer
serialises.

Splitting the SQL from the math keeps the math testable without a
DB. Every function in this module is sync + pure (no async, no IO).
The async layer lives in `app/api/analytics.py`.

Metrics shipped:

  Operational (`/api/dashboard`):
    - processing_time_metrics: avg upload→approval, upload→paid,
      median + p95 variants
    - approval_bottleneck: per-approver pending counts, oldest
      pending, average time-in-queue
    - discount_capture_rate: discount-eligible captured vs missed

  CFO (`/api/analytics/cfo`):
    - days_payable_outstanding (DPO) trend
    - cash_conversion_cycle (DSO + DIO − DPO)
    - accruals: open POs + GR − invoices not yet posted
    - working_capital_impact: "if we paid N days later"
    - supplier_concentration: top-10 / top-50 spend share
    - fraud_rate_trend: exceptions per invoice, by type, monthly
    - early_pay_discount_roi: discount $ captured vs missed
    - rebate_yield: virtual-card rebates / total spend
    - forecast_variance: actual vs forecast outflow
    - cashflow_forecast (`bucket_outflows`): projected AP outflows
      bucketed by day / week / month from committed + pending
      invoices
    - cashflow_whatif (`apply_payment_timing_scenario`): early vs
      on-time vs late payment-timing scenarios, with early-pay
      discount capture
    - cash_position (`compute_cash_position` +
      `detect_threshold_breaches`): running balance from a
      bring-your-own opening balance, with threshold-breach alerts

Drill-through: every metric has a corresponding "give me the rows
that produced this number" function; the API layer exposes those
under `/api/analytics/drill/<metric>`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal


@dataclass(frozen=True)
class ProcessingTimeMetrics:
    """Time-to-X distributions in business days.

    `count_*` is the sample size for each leg; a metric with too
    small a sample (< 5) returns Decimal("0") rather than a wild
    value driven by one outlier."""

    avg_upload_to_approval_days: Decimal
    median_upload_to_approval_days: Decimal
    p95_upload_to_approval_days: Decimal
    avg_upload_to_paid_days: Decimal
    median_upload_to_paid_days: Decimal
    p95_upload_to_paid_days: Decimal
    count_approval_leg: int
    count_paid_leg: int


def _decimal_days(td: timedelta) -> Decimal:
    """Total elapsed days as Decimal with one decimal place."""
    return Decimal(str(round(td.total_seconds() / 86400, 1)))


def _quantile(values: list[Decimal], q: float) -> Decimal:
    """Return the q-th percentile (0 ≤ q ≤ 1). Pure-Python so we
    don't have to add numpy as a dependency for a single number."""
    if not values:
        return Decimal("0")
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    pos = (len(s) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    weight = Decimal(str(pos - lo))
    return (s[lo] * (Decimal("1") - weight) + s[hi] * weight).quantize(Decimal("0.1"))


def _avg(values: list[Decimal]) -> Decimal:
    if not values:
        return Decimal("0")
    return (sum(values, Decimal("0")) / Decimal(len(values))).quantize(Decimal("0.1"))


def compute_processing_time_metrics(
    invoices: list,
    *,
    min_sample: int = 5,
) -> ProcessingTimeMetrics:
    """Inputs are duck-typed invoice rows with `created_at`,
    `approved_at`, and `paid_at` attributes (the latter two
    nullable). Computes avg / median / p95 for both legs.

    Samples smaller than `min_sample` collapse to 0 so a tenant
    with two invoices doesn't see a wildly noisy "avg time to
    pay = 11.7 days" derived from one outlier."""
    approval_days: list[Decimal] = []
    paid_days: list[Decimal] = []
    for inv in invoices:
        created = getattr(inv, "created_at", None)
        if not created:
            continue
        approved = getattr(inv, "approved_at", None)
        if approved is not None:
            approval_days.append(_decimal_days(approved - created))
        paid = getattr(inv, "paid_at", None)
        if paid is not None:
            paid_days.append(_decimal_days(paid - created))

    def _bundle(days: list[Decimal]) -> tuple[Decimal, Decimal, Decimal]:
        if len(days) < min_sample:
            return Decimal("0"), Decimal("0"), Decimal("0")
        return _avg(days), _quantile(days, 0.5), _quantile(days, 0.95)

    avg_a, med_a, p95_a = _bundle(approval_days)
    avg_p, med_p, p95_p = _bundle(paid_days)
    return ProcessingTimeMetrics(
        avg_upload_to_approval_days=avg_a,
        median_upload_to_approval_days=med_a,
        p95_upload_to_approval_days=p95_a,
        avg_upload_to_paid_days=avg_p,
        median_upload_to_paid_days=med_p,
        p95_upload_to_paid_days=p95_p,
        count_approval_leg=len(approval_days),
        count_paid_leg=len(paid_days),
    )


# ---------------------------------------------------------------------------
# Approval bottleneck
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ApprovalBottleneckRow:
    approver_id: str
    approver_name: str | None
    pending_count: int
    oldest_pending_days: Decimal
    avg_pending_days: Decimal


def compute_approval_bottleneck(
    pending_steps: list,
    *,
    now: datetime | None = None,
) -> list[ApprovalBottleneckRow]:
    """Inputs: per-WorkflowStep rows that are `pending` (no
    `completed_at`) and `step_type == "approval"`. Each carries an
    `assigned_to` UUID, an optional `assignee_name` we joined in,
    and a `created_at`.

    Returns one row per approver, sorted descending by pending_count
    so the worst bottleneck is on top. Unassigned steps roll up
    under a synthetic `"unassigned"` key — a non-zero bucket there
    is its own kind of incident (workflow routing broken)."""
    now = now or datetime.now(UTC)
    buckets: dict[str, dict] = {}
    for step in pending_steps:
        approver_id = str(getattr(step, "assigned_to", None) or "unassigned")
        approver_name = getattr(step, "assignee_name", None)
        created = getattr(step, "created_at", None)
        if created is None:
            continue
        age = _decimal_days(now - created)
        b = buckets.setdefault(
            approver_id,
            {
                "approver_name": approver_name,
                "pending_count": 0,
                "ages": [],
            },
        )
        b["pending_count"] += 1
        b["ages"].append(age)

    rows: list[ApprovalBottleneckRow] = []
    for aid, b in buckets.items():
        rows.append(
            ApprovalBottleneckRow(
                approver_id=aid,
                approver_name=b["approver_name"],
                pending_count=b["pending_count"],
                oldest_pending_days=max(b["ages"]),
                avg_pending_days=_avg(b["ages"]),
            )
        )
    return sorted(rows, key=lambda r: (-r.pending_count, -r.oldest_pending_days))


# ---------------------------------------------------------------------------
# Discount capture rate
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DiscountCaptureMetrics:
    """Net of the early-pay discount opportunity for a period."""

    eligible_count: int
    captured_count: int
    missed_count: int
    captured_amount: Decimal
    missed_amount: Decimal
    capture_rate_pct: Decimal


def compute_discount_capture(invoice_rows: list) -> DiscountCaptureMetrics:
    """Inputs: invoice rows that have a `discount_amount` plus
    flags `discount_eligible` and `paid_before_discount_date`.
    We don't model the dates directly here — the SQL layer
    computes the flags from `PaymentSchedule.discount_date` /
    `Payment.completed_at`. This keeps the math layer pure."""
    eligible = 0
    captured = 0
    captured_amt = Decimal("0")
    missed_amt = Decimal("0")
    for r in invoice_rows:
        if not getattr(r, "discount_eligible", False):
            continue
        eligible += 1
        amt = Decimal(str(getattr(r, "discount_amount", "0") or "0"))
        if getattr(r, "paid_before_discount_date", False):
            captured += 1
            captured_amt += amt
        else:
            missed_amt += amt
    missed = eligible - captured
    rate = (
        (Decimal(captured) / Decimal(eligible) * Decimal("100")).quantize(Decimal("0.1"))
        if eligible > 0
        else Decimal("0")
    )
    return DiscountCaptureMetrics(
        eligible_count=eligible,
        captured_count=captured,
        missed_count=missed,
        captured_amount=captured_amt.quantize(Decimal("0.01")),
        missed_amount=missed_amt.quantize(Decimal("0.01")),
        capture_rate_pct=rate,
    )


# ---------------------------------------------------------------------------
# Days Payable Outstanding (DPO)
# ---------------------------------------------------------------------------


def compute_dpo(
    *,
    accounts_payable: Decimal,
    cogs: Decimal,
    period_days: int = 365,
) -> Decimal:
    """Classic DPO = (AP / COGS) × period_days.

    Returns Decimal with one decimal. Zero COGS → 0 (avoids
    divide-by-zero) and warrants an UI annotation that the metric
    isn't computable for this period."""
    if cogs <= 0:
        return Decimal("0")
    return (accounts_payable / cogs * Decimal(period_days)).quantize(Decimal("0.1"))


def compute_dpo_trend(
    monthly_snapshots: list[dict],
    *,
    period_days: int = 30,
) -> list[dict]:
    """`monthly_snapshots` is a list of `{"month": "YYYY-MM",
    "accounts_payable": Decimal, "cogs": Decimal}`. Returns the
    same list with `dpo` appended per row."""
    out: list[dict] = []
    for s in monthly_snapshots:
        ap = Decimal(str(s.get("accounts_payable", "0") or "0"))
        cogs = Decimal(str(s.get("cogs", "0") or "0"))
        out.append(
            {
                **s,
                "dpo": compute_dpo(accounts_payable=ap, cogs=cogs, period_days=period_days),
            }
        )
    return out


# ---------------------------------------------------------------------------
# Cash conversion cycle (DSO + DIO − DPO)
# ---------------------------------------------------------------------------


def compute_cash_conversion_cycle(
    *,
    dso_days: Decimal | None,
    dio_days: Decimal | None,
    dpo_days: Decimal,
) -> Decimal | None:
    """Returns the cycle in days, or None when DSO/DIO aren't
    available (the cycle is only meaningful when all three legs
    are known — AP-only tenants can't compute it)."""
    if dso_days is None or dio_days is None:
        return None
    return (dso_days + dio_days - dpo_days).quantize(Decimal("0.1"))


# ---------------------------------------------------------------------------
# Accruals view
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AccrualsSnapshot:
    """Open commitments awaiting invoice + posting.

    `open_po_amount`: total of POs whose status isn't `closed`
                       and which have outstanding line items.
    `received_amount`: total of GR rows that have been received
                       but for which the matching invoice isn't
                       yet posted to ERP.
    `unposted_invoice_amount`: invoices in approved/sent_to_erp
                       states — accrued liability not yet on the
                       ledger.
    `total_accrual`: open_po_amount + received_amount −
                       unposted_invoice_amount.
    """

    open_po_amount: Decimal
    received_amount: Decimal
    unposted_invoice_amount: Decimal
    total_accrual: Decimal


def compute_accruals(
    *,
    open_po_amount: Decimal,
    received_amount: Decimal,
    unposted_invoice_amount: Decimal,
) -> AccrualsSnapshot:
    total = (open_po_amount + received_amount - unposted_invoice_amount).quantize(Decimal("0.01"))
    return AccrualsSnapshot(
        open_po_amount=open_po_amount.quantize(Decimal("0.01")),
        received_amount=received_amount.quantize(Decimal("0.01")),
        unposted_invoice_amount=unposted_invoice_amount.quantize(Decimal("0.01")),
        total_accrual=total,
    )


# ---------------------------------------------------------------------------
# Working-capital impact: "if we paid N days later, how much cash unlocked?"
# ---------------------------------------------------------------------------


def compute_working_capital_impact(
    *,
    avg_daily_outflow: Decimal,
    days_extended: int,
) -> Decimal:
    """The crude back-of-envelope CFOs ask: extending payment
    terms by N days unlocks `avg_daily_outflow * N` of working
    capital. Real model has terms-by-vendor + late-fee
    considerations; we leave those to the next iteration.
    Returns absolute dollars rounded to whole units."""
    if days_extended <= 0 or avg_daily_outflow <= 0:
        return Decimal("0")
    return (avg_daily_outflow * Decimal(days_extended)).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
    )


# ---------------------------------------------------------------------------
# Supplier concentration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SupplierConcentration:
    """How much spend is concentrated in the top N vendors.

    `flagged` is True iff any single vendor's share exceeds the
    threshold (default 25%) — the canonical concentration-risk
    warning sign."""

    total_spend: Decimal
    top_10_share_pct: Decimal
    top_50_share_pct: Decimal
    largest_vendor: str | None
    largest_vendor_share_pct: Decimal
    flagged: bool


def compute_supplier_concentration(
    vendor_spend: list[dict],
    *,
    concentration_threshold_pct: Decimal = Decimal("25"),
) -> SupplierConcentration:
    """`vendor_spend` is a list of `{"vendor": str, "amount":
    Decimal}` sorted descending by amount.

    Empty input returns a zeroed-out snapshot rather than raising
    — the dashboard renders "no data" rather than 500ing."""
    if not vendor_spend:
        return SupplierConcentration(
            total_spend=Decimal("0"),
            top_10_share_pct=Decimal("0"),
            top_50_share_pct=Decimal("0"),
            largest_vendor=None,
            largest_vendor_share_pct=Decimal("0"),
            flagged=False,
        )
    total = sum(
        (Decimal(str(r.get("amount", "0") or "0")) for r in vendor_spend),
        Decimal("0"),
    )
    if total <= 0:
        return SupplierConcentration(
            total_spend=Decimal("0"),
            top_10_share_pct=Decimal("0"),
            top_50_share_pct=Decimal("0"),
            largest_vendor=None,
            largest_vendor_share_pct=Decimal("0"),
            flagged=False,
        )

    def _share(top_n: list[dict]) -> Decimal:
        s = sum(
            (Decimal(str(r.get("amount", "0") or "0")) for r in top_n),
            Decimal("0"),
        )
        return (s / total * Decimal("100")).quantize(Decimal("0.1"))

    top_10 = _share(vendor_spend[:10])
    top_50 = _share(vendor_spend[:50])
    largest = vendor_spend[0]
    largest_share = (
        Decimal(str(largest.get("amount", "0") or "0")) / total * Decimal("100")
    ).quantize(Decimal("0.1"))
    return SupplierConcentration(
        total_spend=total.quantize(Decimal("0.01")),
        top_10_share_pct=top_10,
        top_50_share_pct=top_50,
        largest_vendor=str(largest.get("vendor") or ""),
        largest_vendor_share_pct=largest_share,
        flagged=largest_share >= concentration_threshold_pct,
    )


# ---------------------------------------------------------------------------
# Fraud-rate trend
# ---------------------------------------------------------------------------


def compute_fraud_rate_trend(
    monthly_buckets: list[dict],
) -> list[dict]:
    """`monthly_buckets`: list of `{"month": "YYYY-MM",
    "invoice_count": int, "exception_count": int,
    "by_type": {type: count}}`. Returns the same shape with
    `rate_pct` appended per row (exceptions / invoices × 100)."""
    out: list[dict] = []
    for b in monthly_buckets:
        inv_count = int(b.get("invoice_count", 0) or 0)
        exc_count = int(b.get("exception_count", 0) or 0)
        rate = (
            (Decimal(exc_count) / Decimal(inv_count) * Decimal("100")).quantize(Decimal("0.1"))
            if inv_count > 0
            else Decimal("0")
        )
        out.append({**b, "rate_pct": rate})
    return out


# ---------------------------------------------------------------------------
# Rebate yield
# ---------------------------------------------------------------------------


def compute_rebate_yield(
    *,
    rebates_total: Decimal,
    total_spend: Decimal,
    months_in_period: int = 12,
) -> dict:
    """Returns the rebate yield as a percentage of spend + an
    annualised run-rate dollars figure (useful for the CFO's
    investor deck)."""
    yield_pct = (
        (rebates_total / total_spend * Decimal("100")).quantize(Decimal("0.01"))
        if total_spend > 0
        else Decimal("0")
    )
    annualised = (
        (rebates_total * Decimal("12") / Decimal(months_in_period)).quantize(Decimal("0.01"))
        if months_in_period > 0
        else Decimal("0")
    )
    return {
        "rebates_total": rebates_total.quantize(Decimal("0.01")),
        "total_spend": total_spend.quantize(Decimal("0.01")),
        "yield_pct": yield_pct,
        "annualised_rebates": annualised,
    }


# ---------------------------------------------------------------------------
# Forecast variance
# ---------------------------------------------------------------------------


def compute_forecast_variance(
    monthly_forecast: list[dict],
) -> list[dict]:
    """`monthly_forecast`: `{"month": "YYYY-MM", "forecast":
    Decimal, "actual": Decimal}`. Adds `variance` (actual − forecast)
    and `variance_pct` per row. Positive variance means we paid out
    MORE than expected — the negative case is the operations win
    (came in under budget)."""
    out: list[dict] = []
    for b in monthly_forecast:
        forecast = Decimal(str(b.get("forecast", "0") or "0"))
        actual = Decimal(str(b.get("actual", "0") or "0"))
        var = (actual - forecast).quantize(Decimal("0.01"))
        var_pct = (
            (var / forecast * Decimal("100")).quantize(Decimal("0.1"))
            if forecast > 0
            else Decimal("0")
        )
        out.append(
            {
                **b,
                "forecast": forecast.quantize(Decimal("0.01")),
                "actual": actual.quantize(Decimal("0.01")),
                "variance": var,
                "variance_pct": var_pct,
            }
        )
    return out


# ---------------------------------------------------------------------------
# Predictive cash-flow forecasting
# ---------------------------------------------------------------------------
#
# These four functions power `/api/analytics/cashflow_forecast`,
# `/cashflow_whatif`, and `/cash_position`. They are pure + sync like the
# rest of this module: the API layer hands in already-fetched commitment
# rows (one per open invoice) and floats the Decimal results at the JSON
# boundary.
#
# A "commitment row" is a dict with:
#   {"due_date": date, "amount": Decimal, "committed": bool,
#    "discount_date": date | None, "discount_percent": Decimal | None}
# `committed` distinguishes firm commitments (approved → payment_scheduled)
# from the still-in-flight pipeline (new / pending / ready_for_review).

_CENTS = Decimal("0.01")


def _q(amount: Decimal) -> Decimal:
    """Quantize to cents, half-up — the money rounding used throughout."""
    return amount.quantize(_CENTS, rounding=ROUND_HALF_UP)


def _period_bounds(d, granularity: str):
    """Return ``(period_key, period_start, period_end)`` for a date under
    the requested bucket granularity.

    - ``day``   → the date itself.
    - ``week``  → the ISO week (Monday-anchored) the date falls in.
    - ``month`` → the calendar month.

    ``period_key`` is a sortable ``YYYY-MM-DD`` string for day/week (week
    keyed on its Monday) and ``YYYY-MM`` for month so periods order
    naturally and group deterministically."""
    from datetime import timedelta

    if granularity == "day":
        return d.isoformat(), d, d
    if granularity == "week":
        monday = d - timedelta(days=d.weekday())
        sunday = monday + timedelta(days=6)
        return monday.isoformat(), monday, sunday
    if granularity == "month":
        start = d.replace(day=1)
        if start.month == 12:
            nxt = start.replace(year=start.year + 1, month=1)
        else:
            nxt = start.replace(month=start.month + 1)
        end = nxt - timedelta(days=1)
        return start.strftime("%Y-%m"), start, end
    raise ValueError(f"unknown granularity {granularity!r}")


def _row_amount(row) -> Decimal:
    return Decimal(str(row.get("amount", "0") or "0"))


def _discount_net(amount: Decimal, discount_percent) -> tuple[Decimal, Decimal]:
    """Return ``(net_outflow, discount_captured)`` for paying ``amount``
    on the discount date. ``discount_percent`` is a whole-number percent
    (e.g. ``Decimal("2")`` = 2%). Missing / non-positive percent → no
    discount (net == amount, captured == 0)."""
    pct = Decimal(str(discount_percent or "0"))
    if pct <= 0:
        return amount, Decimal("0")
    captured = amount * pct / Decimal("100")
    return amount - captured, captured


def bucket_outflows(rows: list[dict], *, granularity: str = "week", today=None) -> list[dict]:
    """Bucket projected AP outflows by ``day`` / ``week`` / ``month``.

    Each row is a commitment dict (see module header). Outflow is timed
    on the row's ``due_date``. Rows with no ``due_date`` are dropped (we
    can't time them). Returns one dict per non-empty period, sorted by
    ``period``:

        {period, period_start, period_end, scheduled_amount,
         committed_amount, pending_amount, discount_eligible_amount,
         count}

    ``today`` is accepted for symmetry / testability but does not filter —
    the API layer is responsible for the horizon window."""
    buckets: dict[str, dict] = {}
    for r in rows:
        due = r.get("due_date")
        if due is None:
            continue
        key, start, end = _period_bounds(due, granularity)
        amount = _row_amount(r)
        b = buckets.setdefault(
            key,
            {
                "period": key,
                "period_start": start,
                "period_end": end,
                "scheduled_amount": Decimal("0"),
                "committed_amount": Decimal("0"),
                "pending_amount": Decimal("0"),
                "discount_eligible_amount": Decimal("0"),
                "count": 0,
            },
        )
        b["scheduled_amount"] += amount
        if r.get("committed"):
            b["committed_amount"] += amount
        else:
            b["pending_amount"] += amount
        if r.get("discount_date") is not None and Decimal(
            str(r.get("discount_percent") or "0")
        ) > 0:
            b["discount_eligible_amount"] += amount
        b["count"] += 1

    out = []
    for key in sorted(buckets):
        b = buckets[key]
        out.append(
            {
                **b,
                "scheduled_amount": _q(b["scheduled_amount"]),
                "committed_amount": _q(b["committed_amount"]),
                "pending_amount": _q(b["pending_amount"]),
                "discount_eligible_amount": _q(b["discount_eligible_amount"]),
            }
        )
    return out


def apply_payment_timing_scenario(
    rows: list[dict],
    *,
    scenario: str,
    granularity: str = "week",
    grace_days: int = 15,
    today=None,
) -> dict:
    """What-if engine for payment timing. ``scenario``:

    - ``on_time`` → pay each row on its ``due_date``, full amount.
    - ``early``   → pay on ``discount_date`` when present, taking the
      ``discount_percent`` reduction; net outflow is reduced and the
      captured discount reported separately. Rows without a discount fall
      back to paying on ``due_date`` at full amount.
    - ``late``    → pay ``due_date + grace_days``, full amount, no
      discount (you forfeit any early-pay discount by paying late).

    Returns::

        {scenario, total_outflow, total_discount_captured,
         weighted_avg_pay_date_days, periods: [bucketed like
         bucket_outflows but on the scenario pay-date + net amount]}

    ``weighted_avg_pay_date_days`` is the amount-weighted mean number of
    days from ``today`` to each row's pay date — a single "how soon does
    the cash leave" number for the scenario card."""
    from datetime import date, timedelta

    if scenario not in ("early", "on_time", "late"):
        raise ValueError(f"unknown scenario {scenario!r}")
    today = today or date.today()

    total_outflow = Decimal("0")
    total_discount = Decimal("0")
    weighted_days = Decimal("0")
    weight_total = Decimal("0")
    # Re-shape each row into a pseudo-commitment timed on the scenario's
    # pay-date with the scenario's net amount, then reuse bucket_outflows.
    timed_rows: list[dict] = []
    for r in rows:
        due = r.get("due_date")
        if due is None:
            continue
        amount = _row_amount(r)
        discount_date = r.get("discount_date")
        if scenario == "early" and discount_date is not None:
            pay_date = discount_date
            net, captured = _discount_net(amount, r.get("discount_percent"))
        elif scenario == "late":
            pay_date = due + timedelta(days=grace_days)
            net, captured = amount, Decimal("0")
        else:  # on_time, or early with no discount available
            pay_date = due
            net, captured = amount, Decimal("0")

        total_outflow += net
        total_discount += captured
        days = Decimal((pay_date - today).days)
        weighted_days += days * net
        weight_total += net
        timed_rows.append(
            {
                "due_date": pay_date,
                "amount": net,
                "committed": r.get("committed", False),
                "discount_date": None,
                "discount_percent": None,
            }
        )

    avg_days = (
        (weighted_days / weight_total).quantize(Decimal("0.1"))
        if weight_total > 0
        else Decimal("0")
    )
    periods = bucket_outflows(timed_rows, granularity=granularity, today=today)
    return {
        "scenario": scenario,
        "total_outflow": _q(total_outflow),
        "total_discount_captured": _q(total_discount),
        "weighted_avg_pay_date_days": avg_days,
        "periods": periods,
    }


def compute_cash_position(
    opening_balance: Decimal,
    outflow_periods: list[dict],
    *,
    inflow_periods: dict | None = None,
    min_balance_threshold: Decimal | None = None,
) -> list[dict]:
    """Running cash balance per period.

    ``outflow_periods`` is the list returned by ``bucket_outflows``.
    ``inflow_periods`` is an optional ``{period_key: Decimal}`` map of
    expected inflows — defaults to empty (AP-only product; receivables
    aren't modelled, mirroring the ``None`` handling in
    ``compute_cash_conversion_cycle``). The closing balance of each period
    carries forward as the next period's opening::

        closing = opening - outflow + inflow

    Returns one row per period::

        {period, period_start, period_end, opening, outflow, inflow,
         closing, below_threshold}

    ``below_threshold`` is True iff a threshold is supplied and the
    period's closing balance falls below it."""
    inflow_periods = inflow_periods or {}
    rows: list[dict] = []
    opening = Decimal(str(opening_balance or "0"))
    for p in outflow_periods:
        outflow = Decimal(str(p.get("scheduled_amount", "0") or "0"))
        inflow = Decimal(str(inflow_periods.get(p["period"], "0") or "0"))
        closing = opening - outflow + inflow
        below = min_balance_threshold is not None and closing < min_balance_threshold
        rows.append(
            {
                "period": p["period"],
                "period_start": p.get("period_start"),
                "period_end": p.get("period_end"),
                "opening": _q(opening),
                "outflow": _q(outflow),
                "inflow": _q(inflow),
                "closing": _q(closing),
                "below_threshold": below,
            }
        )
        opening = closing
    return rows


def detect_threshold_breaches(
    position_rows: list[dict], *, min_balance_threshold: Decimal
) -> list[dict]:
    """Return the subset of ``compute_cash_position`` rows whose closing
    balance dips below ``min_balance_threshold`` — the periods the CFO
    should be alerted about. Each breach reports the shortfall::

        {period, period_start, period_end, closing, shortfall}

    ``shortfall`` is how far below the threshold the period closed
    (always positive)."""
    threshold = Decimal(str(min_balance_threshold))
    breaches: list[dict] = []
    for r in position_rows:
        closing = Decimal(str(r.get("closing", "0") or "0"))
        if closing < threshold:
            breaches.append(
                {
                    "period": r["period"],
                    "period_start": r.get("period_start"),
                    "period_end": r.get("period_end"),
                    "closing": _q(closing),
                    "shortfall": _q(threshold - closing),
                }
            )
    return breaches
