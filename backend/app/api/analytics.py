"""CFO + analytics endpoints.

The operational dashboard at `/api/dashboard` covers AP-clerk
metrics (pipeline, aging, recent payments). This module covers the
CFO surface: DPO trend, cash-conversion cycle, accruals, supplier
concentration, fraud-rate trend, rebate yield, forecast variance,
and the drill-through endpoints that let the CFO click through to
the contributing invoice / payment set.

Computation lives in `app/services/analytics.py` (pure functions).
This file does the SQL and the response shaping.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.api.deps import ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO, require_roles
from app.database import get_control_db
from app.models.expense import Expense, ExpenseReport
from app.models.gl_account import GLAccount
from app.models.invoice import Invoice, InvoiceStatus
from app.models.organization import Organization
from app.models.payment import Payment, PaymentSchedule
from app.models.user import User
from app.models.virtual_card import CardRebate, VirtualCard
from app.services.analytics import (
    OPEN_AP_STATUSES,
    ReceivedPO,
    apply_payment_timing_scenario,
    bucket_outflows,
    compute_accruals,
    compute_cash_conversion_cycle,
    compute_cash_position,
    compute_dpo,
    compute_forecast_variance,
    compute_fraud_rate_trend,
    compute_rebate_yield,
    compute_supplier_concentration,
    compute_working_capital_impact,
    detect_threshold_breaches,
    value_received_goods,
)
from app.services.audit_dispatch import dispatch_auth_audit
from app.services.cashflow import (
    CashThresholds,
    fetch_provider_balance,
    resolve_cash_thresholds,
    store_cash_thresholds,
)
from app.services.currency_conversion import (
    compute_unrealized_fx_gain_loss,
    resolve_reporting_currency,
    rollup_to_reporting_currency,
    vendor_rollup_to_reporting_currency,
)
from app.services.fx_adapters import get_fx_adapter
from app.tenant import apply_entity_scope, get_entity_id, get_tenant, get_tenant_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/analytics", tags=["analytics"])


# Most metrics are read-mostly heavy queries — CFO + admin only.
# AP managers see the operational dashboard but not the CFO surface
# unless the org explicitly grants them.
_CFO_ROLES = (ROLE_ADMIN, ROLE_CFO)


# ---------------------------------------------------------------------------
# Predictive cash-flow forecasting
# ---------------------------------------------------------------------------
#
# Committed = firm AP commitment (approved → payment_scheduled). Pending =
# still-in-flight pipeline (new / pending / ready_for_review) — projected,
# lower-certainty outflow. Everything else (rejected / paid / done /
# failed) is excluded: terminal, already-paid, or never going to pay.
_COMMITTED_STATUSES = (
    InvoiceStatus.approved.value,
    InvoiceStatus.sending_to_erp.value,
    InvoiceStatus.sent_to_erp.value,
    InvoiceStatus.posted_in_erp.value,
    InvoiceStatus.payment_scheduled.value,
)
_PENDING_STATUSES = (
    InvoiceStatus.new.value,
    InvoiceStatus.pending.value,
    InvoiceStatus.ready_for_review.value,
)


async def _commitment_rows(
    db: AsyncSession,
    *,
    today: date,
    horizon_days: int,
    include_pending: bool,
    entity_id: uuid.UUID | None = None,
) -> list[dict]:
    """Pull open invoices that represent future AP outflows and shape them
    into the commitment-row dicts the pure-math layer consumes.

    Outflow timing comes from the `PaymentSchedule` row when present
    (`due_date` / `discount_date` / `discount_percent`); otherwise we fall
    back to `Invoice.due_date` with no discount. Rows are bounded to
    `[today, today + horizon_days]` on the effective due date so the query
    doesn't scan the whole back-catalogue.

    Scoped to ``entity_id`` (the invoice's subsidiary) when set; ``None`` is
    the consolidated view (multi-entity Phase 2b)."""
    statuses = list(_COMMITTED_STATUSES)
    if include_pending:
        statuses += list(_PENDING_STATUSES)
    horizon_end = today + timedelta(days=horizon_days)

    result = await db.execute(
        apply_entity_scope(
            select(
                Invoice.id,
                Invoice.amount,
                Invoice.status,
                Invoice.due_date,
                PaymentSchedule.due_date.label("sched_due"),
                PaymentSchedule.discount_date,
                PaymentSchedule.discount_percent,
            )
            .outerjoin(PaymentSchedule, PaymentSchedule.invoice_id == Invoice.id)
            .where(Invoice.status.in_(statuses)),
            Invoice,
            entity_id,
        )
    )
    committed_set = set(_COMMITTED_STATUSES)
    rows: list[dict] = []
    for inv_id, amount, status, inv_due, sched_due, discount_date, discount_percent in result.all():
        due = sched_due or inv_due
        if due is None or due < today or due > horizon_end:
            continue
        status_value = status.value if hasattr(status, "value") else status
        rows.append(
            {
                "invoice_id": str(inv_id),
                "due_date": due,
                "amount": Decimal(str(amount or 0)),
                "committed": status_value in committed_set,
                "discount_date": discount_date,
                "discount_percent": discount_percent,
            }
        )
    return rows


def _parse_decimal_param(raw: str | None, field: str) -> Decimal | None:
    """Parse an optional money query-param into Decimal, 400 on garbage.
    Used for `opening_balance` / `min_balance_threshold` — passed as
    strings so we never round-trip currency through a float."""
    if raw is None or raw == "":
        return None
    try:
        return Decimal(raw)
    except (ArithmeticError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"`{field}` must be a number") from exc


@router.get("/cashflow_forecast")
async def get_cashflow_forecast(
    granularity: str = Query("week", pattern="^(day|week|month)$"),
    horizon_days: int = Query(90, ge=7, le=730),
    include_pending: bool = Query(True),
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(*_CFO_ROLES)),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    """Projected AP cash outflows bucketed by `day` / `week` / `month`
    over the next `horizon_days`. Each period splits the scheduled total
    into firm `committed_amount` and lower-certainty `pending_amount`
    (the in-flight approval pipeline; drop it with `include_pending=false`)
    and reports the discount-eligible slice."""
    today = date.today()
    rows = await _commitment_rows(
        db,
        today=today,
        horizon_days=horizon_days,
        include_pending=include_pending,
        entity_id=entity_id,
    )
    periods = bucket_outflows(rows, granularity=granularity, today=today)
    totals = {
        "scheduled_amount": float(sum((p["scheduled_amount"] for p in periods), Decimal("0"))),
        "committed_amount": float(sum((p["committed_amount"] for p in periods), Decimal("0"))),
        "pending_amount": float(sum((p["pending_amount"] for p in periods), Decimal("0"))),
        "discount_eligible_amount": float(
            sum((p["discount_eligible_amount"] for p in periods), Decimal("0"))
        ),
        "count": sum(p["count"] for p in periods),
    }
    return {
        "granularity": granularity,
        "horizon_days": horizon_days,
        "include_pending": include_pending,
        "generated_at": datetime.now(UTC).isoformat(),
        "periods": [
            {
                "period": p["period"],
                "period_start": p["period_start"].isoformat(),
                "period_end": p["period_end"].isoformat(),
                "scheduled_amount": float(p["scheduled_amount"]),
                "committed_amount": float(p["committed_amount"]),
                "pending_amount": float(p["pending_amount"]),
                "discount_eligible_amount": float(p["discount_eligible_amount"]),
                "count": p["count"],
            }
            for p in periods
        ],
        "totals": totals,
    }


@router.get("/cashflow_whatif")
async def get_cashflow_whatif(
    granularity: str = Query("week", pattern="^(day|week|month)$"),
    horizon_days: int = Query(90, ge=7, le=730),
    grace_days: int = Query(15, ge=0, le=90),
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(*_CFO_ROLES)),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    """Payment-timing what-if: compares paying every open commitment
    `early` (on the discount date, capturing the early-pay discount),
    `on_time` (on the due date), and `late` (`due_date + grace_days`,
    forfeiting any discount). Each scenario reports its total outflow,
    discount captured, amount-weighted average days-to-pay, and the
    bucketed period breakdown."""
    today = date.today()
    rows = await _commitment_rows(
        db, today=today, horizon_days=horizon_days, include_pending=True, entity_id=entity_id
    )

    def _serialise(result: dict) -> dict:
        return {
            "scenario": result["scenario"],
            "total_outflow": float(result["total_outflow"]),
            "total_discount_captured": float(result["total_discount_captured"]),
            "weighted_avg_pay_date_days": float(result["weighted_avg_pay_date_days"]),
            "periods": [
                {
                    "period": p["period"],
                    "period_start": p["period_start"].isoformat(),
                    "period_end": p["period_end"].isoformat(),
                    "scheduled_amount": float(p["scheduled_amount"]),
                }
                for p in result["periods"]
            ],
        }

    scenarios = {
        name: _serialise(
            apply_payment_timing_scenario(
                rows,
                scenario=name,
                granularity=granularity,
                grace_days=grace_days,
                today=today,
            )
        )
        for name in ("early", "on_time", "late")
    }
    return {
        "granularity": granularity,
        "horizon_days": horizon_days,
        "grace_days": grace_days,
        "scenarios": scenarios,
    }


@router.get("/cash_position")
async def get_cash_position(
    granularity: str = Query("week", pattern="^(day|week|month)$"),
    horizon_days: int = Query(90, ge=7, le=730),
    opening_balance: str | None = Query(None),
    min_balance_threshold: str | None = Query(None),
    seed_balance: bool = Query(True),
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(*_CFO_ROLES)),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    """Running cash-position projection: opening balance carried forward
    period-by-period minus scheduled AP outflows, flagging periods that
    close below `min_balance_threshold`.

    Opening-balance resolution (first hit wins):
      1. `opening_balance` query param (explicit BYO override) → `"query"`.
      2. Auto-sync from the org's configured payment/banking provider when its
         adapter supports the optional `get_balance` capability (the `mock`
         adapter returns a deterministic figure for local dev) → `"provider"`.
         Best-effort: a fetch failure / unsupported adapter silently falls
         through. Pass `seed_balance=false` to skip the provider call.
      3. Persisted `Organization.settings.cashflow.opening_balance` → `"settings"`.
      4. `0` with `opening_balance_source: "none"` so the UI prompts for one.

    The alert threshold is the `min_balance_threshold` query param when supplied,
    else the org's persisted `settings.cashflow.min_balance_threshold` (managed
    via `GET/PUT /api/analytics/cash-position-settings`). Inflows (receivables)
    aren't modelled — `closing = opening - outflow`."""
    today = date.today()
    rows = await _commitment_rows(
        db, today=today, horizon_days=horizon_days, include_pending=True, entity_id=entity_id
    )
    periods = bucket_outflows(rows, granularity=granularity, today=today)

    opening = _parse_decimal_param(opening_balance, "opening_balance")
    source = "query"
    balance_currency: str | None = None
    payments_config = (org.settings or {}).get("payments")
    if opening is None and seed_balance and payments_config:
        # Auto-sync: pull the live funding-account balance from the configured
        # provider when its adapter supports it. Best-effort — None on any
        # failure / unsupported adapter, so we fall through to the manual chain.
        # Skipped entirely when the org has configured no payments provider (a
        # bare clone shouldn't fabricate a balance from the mock fallback).
        provider_balance = await fetch_provider_balance(payments_config)
        if provider_balance is not None:
            opening = provider_balance.amount
            balance_currency = provider_balance.currency
            source = "provider"
    if opening is None:
        settings_balance = (org.settings or {}).get("cashflow", {}).get("opening_balance")
        if settings_balance is not None:
            opening = _parse_decimal_param(str(settings_balance), "opening_balance")
            source = "settings"
    if opening is None:
        opening = Decimal("0")
        source = "none"

    threshold = _parse_decimal_param(min_balance_threshold, "min_balance_threshold")
    if threshold is None:
        # No per-request override → fall back to the org's persisted threshold.
        threshold = resolve_cash_thresholds(org.settings).min_balance_threshold
    position = compute_cash_position(opening, periods, min_balance_threshold=threshold)
    breaches = (
        detect_threshold_breaches(position, min_balance_threshold=threshold)
        if threshold is not None
        else []
    )
    return {
        "granularity": granularity,
        "horizon_days": horizon_days,
        "opening_balance": float(opening),
        "opening_balance_source": source,
        "opening_balance_currency": balance_currency,
        "threshold": float(threshold) if threshold is not None else None,
        "periods": [
            {
                "period": p["period"],
                "period_start": p["period_start"].isoformat() if p["period_start"] else None,
                "period_end": p["period_end"].isoformat() if p["period_end"] else None,
                "opening": float(p["opening"]),
                "outflow": float(p["outflow"]),
                "inflow": float(p["inflow"]),
                "closing": float(p["closing"]),
                "below_threshold": p["below_threshold"],
            }
            for p in position
        ],
        "breaches": [
            {
                "period": b["period"],
                "period_start": b["period_start"].isoformat() if b["period_start"] else None,
                "period_end": b["period_end"].isoformat() if b["period_end"] else None,
                "closing": float(b["closing"]),
                "shortfall": float(b["shortfall"]),
            }
            for b in breaches
        ],
    }


# ---------------------------------------------------------------------------
# /api/analytics/cash-position-settings — persisted alert thresholds
# ---------------------------------------------------------------------------


class CashThresholdSettings(BaseModel):
    """Per-org persisted cash-position alert thresholds.

    `min_balance_threshold` is the low-balance warning level (exact `Decimal`,
    serialised as a JSON string so it never round-trips through a float).
    `null` means "no persisted threshold"."""

    min_balance_threshold: Decimal | None = None

    @field_validator("min_balance_threshold")
    @classmethod
    def _non_negative(cls, v: Decimal | None) -> Decimal | None:
        if v is not None and v < 0:
            raise ValueError("min_balance_threshold must be >= 0")
        return v


def _threshold_response(thresholds: CashThresholds) -> dict:
    """Serialise persisted thresholds as JSON strings (money never as float)."""
    return {
        "min_balance_threshold": (
            str(thresholds.min_balance_threshold)
            if thresholds.min_balance_threshold is not None
            else None
        ),
    }


@router.get("/cash-position-settings")
async def get_cash_position_settings(
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(*_CFO_ROLES)),
):
    """Return this org's persisted cash-position alert thresholds.

    Read-gated to the same CFO + admin surface as the cash-position view. The
    cash-position endpoint reads `min_balance_threshold` from here whenever the
    request doesn't pass its own override."""
    return _threshold_response(resolve_cash_thresholds(org.settings))


@router.put("/cash-position-settings")
async def update_cash_position_settings(
    body: CashThresholdSettings,
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(*_CFO_ROLES)),
    db: AsyncSession = Depends(get_control_db),
):
    """Persist this org's cash-position alert thresholds on
    `Organization.settings.cashflow` (JSON — no migration). CFO + admin; audited.

    Money is stored as a JSON string (never a float) and preserves any other
    keys already on the `cashflow` block (e.g. a manually set `opening_balance`).
    Audit details record the new threshold only — no PII."""
    thresholds = CashThresholds(min_balance_threshold=body.min_balance_threshold)
    org.settings = store_cash_thresholds(org.settings, thresholds)
    # Mutating nested JSONB in-place doesn't mark the column dirty on its own.
    flag_modified(org, "settings")
    await db.commit()

    await dispatch_auth_audit(
        organization_id=org.id,
        actor_id=user.id,
        action="organization.cash_thresholds_updated",
        entity_id=org.id,
        details={
            "min_balance_threshold": (
                str(thresholds.min_balance_threshold)
                if thresholds.min_balance_threshold is not None
                else None
            ),
        },
    )
    return _threshold_response(thresholds)


# ---------------------------------------------------------------------------
# /api/analytics/cfo — the aggregate CFO dashboard
# ---------------------------------------------------------------------------


@router.get("/cfo")
async def get_cfo_analytics(
    period_days: int = Query(365, ge=30, le=730),
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(*_CFO_ROLES)),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    """Aggregate every CFO metric into one response so the dashboard
    can render in a single round-trip. Per-metric drill-through is
    still available under `/api/analytics/drill/<metric>`.

    `period_days` defaults to a trailing 365 (annual view); the CFO
    can flip to 90/180 from the UI. Anything outside [30, 730] is
    refused — a 1-day analytics window is noise, a 5-year window
    is a query the operational DB shouldn't be running.

    Every Invoice/Payment/Exception/PO query is entity-scoped via the
    `_inv` / `_pay` / `_exc` helpers (None = consolidated). `CardRebate` is
    tenant-scoped like everything else here (not control-plane) and is
    scoped the same way, via a join to `VirtualCard` (which carries
    `entity_id`, `CardRebate` itself does not)."""
    today = date.today()
    period_start = today - timedelta(days=period_days)

    def _inv(q):
        return apply_entity_scope(q, Invoice, entity_id)

    def _pay(q):
        return apply_entity_scope(q, Payment, entity_id)

    def _exc(q, model):
        return apply_entity_scope(q, model, entity_id)

    # ----- Total spend in window + accounts-payable balance -----
    # `total_spend` is a DIFFERENT population from the web dashboard's
    # "Total Amount" KPI (`GET /api/dashboard`'s `total_amount` — every
    # invoice, any status, no date bound): this figure is windowed to the
    # trailing `period_days` and excludes only `rejected`. Don't treat the two
    # as interchangeable in a UI or export — see backend/docs/analytics.md and
    # `tests/test_analytics_rejected_exclusion.py`.
    total_spend_q = await db.execute(
        _inv(
            select(func.coalesce(func.sum(Invoice.amount), 0)).where(
                Invoice.invoice_date >= period_start,
                Invoice.status != InvoiceStatus.rejected.value,
            )
        )
    )
    total_spend = Decimal(str(total_spend_q.scalar() or 0))

    # ----- Multi-currency reporting rollup of spend in window -----
    # The naive SUM above mixes currencies. Re-roll into the org's reporting
    # currency using each row's rate-locked `reporting_amount` so a EUR invoice
    # and a USD invoice add up correctly. See backend/docs/multi-currency.md.
    reporting_currency = resolve_reporting_currency(org.settings)
    spend_rows_q = await db.execute(
        _inv(
            select(
                Invoice.amount,
                Invoice.currency,
                Invoice.reporting_amount,
                Invoice.reporting_currency,
            ).where(
                Invoice.invoice_date >= period_start,
                Invoice.status != InvoiceStatus.rejected.value,
            )
        )
    )
    spend_rollup = rollup_to_reporting_currency(
        [
            {"amount": r[0], "currency": r[1], "reporting_amount": r[2], "reporting_currency": r[3]}
            for r in spend_rows_q.all()
        ],
        reporting_currency=reporting_currency,
    )

    ap_balance_q = await db.execute(
        _inv(
            select(func.coalesce(func.sum(Invoice.amount), 0)).where(
                Invoice.status.in_(OPEN_AP_STATUSES)
            )
        )
    )
    ap_balance = Decimal(str(ap_balance_q.scalar() or 0))

    # ----- DPO (using `total_spend` as a COGS proxy when the org -----
    # ----- doesn't surface real COGS data — the dashboard tile -----
    # ----- annotates this as a proxy estimate). -----
    dpo = compute_dpo(accounts_payable=ap_balance, cogs=total_spend, period_days=period_days)

    # ----- DPO trend (last 6 months snapshots) -----
    monthly_dpo_rows = []
    cursor = today.replace(day=1)
    for _ in range(6):
        month_end = cursor - timedelta(days=1)
        month_start = month_end.replace(day=1)
        month_spend_q = await db.execute(
            _inv(
                select(func.coalesce(func.sum(Invoice.amount), 0)).where(
                    Invoice.invoice_date >= month_start,
                    Invoice.invoice_date <= month_end,
                    # Exclude rejected — match the headline total_spend, else the
                    # DPO trend's COGS proxy is inflated vs the current-period DPO.
                    Invoice.status != InvoiceStatus.rejected.value,
                )
            )
        )
        month_ap_q = await db.execute(
            _inv(
                select(func.coalesce(func.sum(Invoice.amount), 0)).where(
                    Invoice.invoice_date <= month_end,
                    Invoice.status.in_(
                        [
                            InvoiceStatus.approved.value,
                            InvoiceStatus.sending_to_erp.value,
                            InvoiceStatus.sent_to_erp.value,
                            InvoiceStatus.posted_in_erp.value,
                            InvoiceStatus.payment_scheduled.value,
                        ]
                    ),
                )
            )
        )
        cogs_m = Decimal(str(month_spend_q.scalar() or 0))
        feoh_m = Decimal(str(month_ap_q.scalar() or 0))
        monthly_dpo_rows.append(
            {
                "month": month_start.strftime("%Y-%m"),
                "dpo": compute_dpo(accounts_payable=feoh_m, cogs=cogs_m, period_days=30),
            }
        )
        cursor = month_start
    monthly_dpo_rows.reverse()

    # ----- Cash conversion cycle — DSO/DIO aren't available in -----
    # ----- an AP-only product. We expose None so the UI can render -----
    # ----- "needs receivables data". -----
    ccc = compute_cash_conversion_cycle(dso_days=None, dio_days=None, dpo_days=dpo)

    # ----- Accruals — open POs, GRs, unposted invoices -----
    # `_open_po_sum_query` keeps the failure surface narrow on tenants that
    # never enabled PO matching (returns a literal-0 query) and entity-scopes
    # the PO sum when a subsidiary is selected.
    open_po_q = await db.execute(_open_po_sum_query(entity_id))
    try:
        open_po_amount = Decimal(str(open_po_q.scalar() or 0))
    except Exception:  # noqa: BLE001
        open_po_amount = Decimal("0")

    # Unposted invoices: approved + sending_to_erp + sent_to_erp
    unposted_q = await db.execute(
        _inv(
            select(func.coalesce(func.sum(Invoice.amount), 0)).where(
                Invoice.status.in_(
                    [
                        InvoiceStatus.approved.value,
                        InvoiceStatus.sending_to_erp.value,
                        InvoiceStatus.sent_to_erp.value,
                    ]
                )
            )
        )
    )
    unposted_invoice_amount = Decimal(str(unposted_q.scalar() or 0))
    # GR-received-but-not-invoiced: fan the 3-way match out per-PO and
    # value the received fraction of each receipted PO. `_received_amount`
    # fails soft to 0 on tenants without procurement tables.
    received_amount = await _received_amount(db, entity_id)
    accruals = compute_accruals(
        open_po_amount=open_po_amount,
        received_amount=received_amount,
        unposted_invoice_amount=unposted_invoice_amount,
    )

    # ----- Working-capital impact (extend by 5 days) -----
    paid_q = await db.execute(
        _pay(
            select(func.coalesce(func.sum(Payment.amount), 0)).where(
                Payment.completed_at
                >= datetime.combine(period_start, datetime.min.time()).replace(tzinfo=UTC),
                Payment.status == "completed",
            )
        )
    )
    paid_in_period = Decimal(str(paid_q.scalar() or 0))
    days_in_period = max(period_days, 1)
    avg_daily_outflow = (paid_in_period / Decimal(days_in_period)).quantize(Decimal("0.01"))
    wc_impact_5d = compute_working_capital_impact(
        avg_daily_outflow=avg_daily_outflow, days_extended=5
    )

    # ----- Supplier concentration (top-10 / top-50) -----
    # Rolled into the org's reporting currency (not a naive SUM across
    # currencies) — a vendor billing in more than one currency used to add
    # e.g. USD + EUR as if they were one currency.
    vendor_rows = await db.execute(
        _inv(
            select(
                Invoice.vendor_name,
                Invoice.amount,
                Invoice.currency,
                Invoice.reporting_amount,
                Invoice.reporting_currency,
            ).where(
                Invoice.invoice_date >= period_start,
                Invoice.vendor_name.isnot(None),
                Invoice.vendor_name != "",
                # Exclude rejected so the concentration denominator matches the
                # headline total_spend — else vendor shares are understated.
                Invoice.status != InvoiceStatus.rejected.value,
            )
        )
    )
    vendor_entries = vendor_rollup_to_reporting_currency(
        [
            {
                "vendor": vendor,
                "amount": amount,
                "currency": currency,
                "reporting_amount": rep_amt,
                "reporting_currency": rep_cur,
            }
            for vendor, amount, currency, rep_amt, rep_cur in vendor_rows.all()
        ],
        reporting_currency=reporting_currency,
    )
    vendor_spend = [{"vendor": e.vendor, "amount": e.amount} for e in vendor_entries[:50]]
    concentration = compute_supplier_concentration(vendor_spend)

    # ----- Fraud-rate trend (last 6 months) — proxy: exception -----
    # ----- count / invoice count per month. -----
    fraud_rows: list[dict] = []
    cursor = today.replace(day=1)
    for _ in range(6):
        month_end = cursor - timedelta(days=1)
        month_start = month_end.replace(day=1)
        inv_count_q = await db.execute(
            _inv(
                select(func.count(Invoice.id)).where(
                    Invoice.invoice_date >= month_start,
                    Invoice.invoice_date <= month_end,
                )
            )
        )
        exc_count = 0
        try:
            from app.models.exception import Exception as APException

            exc_count_q = await db.execute(
                _exc(
                    select(func.count(APException.id)).where(
                        APException.created_at
                        >= datetime.combine(month_start, datetime.min.time()).replace(tzinfo=UTC),
                        APException.created_at
                        <= datetime.combine(month_end, datetime.max.time()).replace(tzinfo=UTC),
                    ),
                    APException,
                )
            )
            exc_count = int(exc_count_q.scalar() or 0)
        except Exception:  # noqa: BLE001
            exc_count = 0
        fraud_rows.append(
            {
                "month": month_start.strftime("%Y-%m"),
                "invoice_count": int(inv_count_q.scalar() or 0),
                "exception_count": exc_count,
                "by_type": {},
            }
        )
        cursor = month_start
    fraud_rows.reverse()
    fraud_trend = compute_fraud_rate_trend(fraud_rows)

    # ----- Rebate yield -----
    try:
        rebate_q = await db.execute(
            apply_entity_scope(
                select(func.coalesce(func.sum(CardRebate.amount), 0)).join(
                    VirtualCard, CardRebate.virtual_card_id == VirtualCard.id
                ),
                VirtualCard,
                entity_id,
            )
        )
        rebates_total = Decimal(str(rebate_q.scalar() or 0))
    except Exception:  # noqa: BLE001
        rebates_total = Decimal("0")
    rebate = compute_rebate_yield(
        rebates_total=rebates_total,
        total_spend=total_spend,
        months_in_period=max(period_days // 30, 1),
    )

    # ----- Unrealized FX gain/loss on OPEN foreign-currency invoices -----
    # Realized FX gain/loss is measured at payment time by
    # `international_payments.compute_fx_gain_loss`. This is the reporting-layer
    # companion: for approved-but-unpaid foreign invoices we mark the open
    # liability to today's rate and report the difference vs the booked
    # (rate-locked) reporting amount. One FX call per distinct foreign currency;
    # best-effort so an FX outage doesn't 500 the whole CFO dashboard.
    open_statuses = OPEN_AP_STATUSES
    unrealized_payload: dict = {
        "reporting_currency": reporting_currency,
        # Money flows through Decimal even for the fallback zero (money
        # invariant), serialised to float at this JSON boundary like every
        # other figure in this response. The real "FX unavailable vs a
        # genuine zero gain/loss" signal is `available` (flipped False in the
        # except branch below) — never this number.
        "total_unrealized_gain_loss": float(Decimal("0")),
        "by_currency": [],
        "available": True,
    }
    try:
        open_rows_q = await db.execute(
            _inv(
                select(
                    Invoice.amount,
                    Invoice.currency,
                    Invoice.reporting_amount,
                    Invoice.reporting_currency,
                ).where(Invoice.status.in_(open_statuses))
            )
        )
        open_invoices = [
            {"amount": r[0], "currency": r[1], "reporting_amount": r[2], "reporting_currency": r[3]}
            for r in open_rows_q.all()
        ]
        fx_adapter = get_fx_adapter((org.settings or {}).get("fx"))
        unrealized = await compute_unrealized_fx_gain_loss(
            open_invoices,
            reporting_currency=reporting_currency,
            fx_adapter=fx_adapter,
        )
        unrealized_payload = {
            "reporting_currency": unrealized.reporting_currency,
            "total_unrealized_gain_loss": float(unrealized.total_unrealized_gain_loss),
            "by_currency": [
                {
                    "currency": e.currency,
                    "open_original_amount": float(e.open_original_amount),
                    "booked_reporting_amount": float(e.booked_reporting_amount),
                    "current_reporting_amount": float(e.current_reporting_amount),
                    "unrealized_gain_loss": float(e.unrealized_gain_loss),
                }
                for e in unrealized.by_currency
            ],
            "available": True,
        }
    except Exception:  # noqa: BLE001 — FX outage shouldn't break the dashboard
        logger.warning("unrealized FX gain/loss unavailable; FX lookup failed")
        unrealized_payload["available"] = False

    return {
        "period_days": period_days,
        "period_start": period_start.isoformat(),
        "total_spend": float(total_spend),
        # Currency-aware spend rollup (the unified reporting-currency total +
        # the per-currency split). `total_spend` above stays as the legacy
        # naive SUM for back-compat; `reporting_spend.total_amount` is the
        # figure to trust when the org books in multiple currencies.
        "reporting_spend": {
            "reporting_currency": spend_rollup.reporting_currency,
            "total_amount": float(spend_rollup.total_reporting_amount),
            "total_count": spend_rollup.total_count,
            "unconverted_count": spend_rollup.unconverted_count,
            "by_currency": [
                {
                    "currency": e.currency,
                    "original_amount": float(e.original_amount),
                    "reporting_amount": float(e.reporting_amount),
                    "count": e.count,
                    "unconverted_count": e.unconverted_count,
                }
                for e in spend_rollup.by_currency
            ],
        },
        "unrealized_fx": unrealized_payload,
        "accounts_payable_balance": float(ap_balance),
        "dpo_current": float(dpo),
        "dpo_trend": [{"month": r["month"], "dpo": float(r["dpo"])} for r in monthly_dpo_rows],
        "cash_conversion_cycle": float(ccc) if ccc is not None else None,
        "accruals": {
            "open_po_amount": float(accruals.open_po_amount),
            "received_amount": float(accruals.received_amount),
            "unposted_invoice_amount": float(accruals.unposted_invoice_amount),
            "total_accrual": float(accruals.total_accrual),
        },
        "working_capital_impact_5_days": float(wc_impact_5d),
        "avg_daily_outflow": float(avg_daily_outflow),
        "supplier_concentration": {
            "total_spend": float(concentration.total_spend),
            "top_10_share_pct": float(concentration.top_10_share_pct),
            "top_50_share_pct": float(concentration.top_50_share_pct),
            "largest_vendor": concentration.largest_vendor,
            "largest_vendor_share_pct": float(concentration.largest_vendor_share_pct),
            "flagged": concentration.flagged,
        },
        "fraud_rate_trend": [{**r, "rate_pct": float(r["rate_pct"])} for r in fraud_trend],
        "rebate_yield": {
            "rebates_total": float(rebate["rebates_total"]),
            "total_spend": float(rebate["total_spend"]),
            "yield_pct": float(rebate["yield_pct"]),
            "annualised_rebates": float(rebate["annualised_rebates"]),
        },
    }


def _open_po_sum_query(entity_id: uuid.UUID | None):
    """Build the open-PO accrual sum query, entity-scoped when a subsidiary is
    selected. Lazily imports PurchaseOrder so a tenant without procurement
    tables doesn't blow up — it falls back to a literal-0 sum. Keeping the
    import lazy also keeps the failure surface narrow on those tenants."""
    try:
        from app.models.procurement import PurchaseOrder

        return apply_entity_scope(
            select(func.coalesce(func.sum(PurchaseOrder.total), 0)), PurchaseOrder, entity_id
        )
    except Exception:  # noqa: BLE001
        from sqlalchemy import literal

        return select(func.coalesce(func.sum(literal(0)), 0))


async def _received_amount(db: AsyncSession, entity_id: uuid.UUID | None) -> Decimal:
    """Value goods received but not yet invoiced (the GR/IR accrual leg).

    Loads every `received` goods receipt (entity-scoped) with its line
    items, aggregates received quantity per PO, then values the received
    fraction of each PO via `value_received_goods`. Lazily imports the
    procurement models so a tenant without those tables falls back to 0
    rather than 500-ing (mirrors `_open_po_sum_query`). Receipts with no
    PO link can't be priced and are excluded.
    """
    try:
        from collections import defaultdict

        from sqlalchemy.orm import selectinload

        from app.models.procurement import GoodsReceipt, PurchaseOrder

        gr_rows = (
            (
                await db.execute(
                    apply_entity_scope(
                        select(GoodsReceipt)
                        .where(GoodsReceipt.status == "received")
                        .options(selectinload(GoodsReceipt.line_items)),
                        GoodsReceipt,
                        entity_id,
                    )
                )
            )
            .scalars()
            .all()
        )

        gr_qty_by_po: dict[uuid.UUID, Decimal] = defaultdict(lambda: Decimal("0"))
        for gr in gr_rows:
            if not gr.po_id:
                continue
            gr_qty_by_po[gr.po_id] += sum(
                (li.quantity_received or Decimal("0") for li in gr.line_items), Decimal("0")
            )
        if not gr_qty_by_po:
            return Decimal("0")

        # Entity scope is carried transitively: the PO ids come only from the
        # entity-scoped GR query above, so this fetch is already confined to the
        # selected subsidiary. Keep the GR query's `apply_entity_scope` if you
        # touch this — dropping it would silently widen this fetch too.
        po_rows = (
            (
                await db.execute(
                    select(PurchaseOrder)
                    .where(PurchaseOrder.id.in_(gr_qty_by_po.keys()))
                    .options(selectinload(PurchaseOrder.line_items))
                )
            )
            .scalars()
            .all()
        )

        receipts = [
            ReceivedPO(
                po_total=po.total or Decimal("0"),
                po_qty_total=sum(
                    (li.quantity or Decimal("0") for li in po.line_items), Decimal("0")
                ),
                gr_qty_total=gr_qty_by_po[po.id],
            )
            for po in po_rows
        ]
        return value_received_goods(receipts)
    except Exception:  # noqa: BLE001
        return Decimal("0")


# ---------------------------------------------------------------------------
# /api/analytics/by-entity — per-entity rollup + consolidated cross-check
# ---------------------------------------------------------------------------

# Open (still-payable) statuses — the AP balance / outstanding leg. Aliased to
# the canonical `OPEN_AP_STATUSES` (services.analytics) so the per-entity rollup,
# the /cfo accounts_payable_balance, and the aging buckets all count
# "outstanding" identically and reconcile.
_OPEN_AP_STATUSES = OPEN_AP_STATUSES


async def _entity_metrics(
    db: AsyncSession,
    *,
    entity_id: uuid.UUID | None,
    period_start: date,
) -> dict:
    """Compute the per-entity AP rollup for one ``entity_id`` (``None`` =
    consolidated across every entity). Reuses the same entity-scoped query
    shapes as ``/analytics/cfo`` — total spend in window, open-payables
    balance, invoice count, open-exception count, open-PO accrual — so a
    per-entity row and the consolidated row are computed identically and the
    consolidated block is a true sum-across-entities cross-check.

    Money is returned as string-Decimal (never float); the route serialises
    it through directly. Lazily-imported models (Exception, PurchaseOrder)
    fail soft to zero on tenants without those tables, mirroring ``/cfo``.
    """

    def _inv(q):
        return apply_entity_scope(q, Invoice, entity_id)

    # Total spend in the trailing window (excludes rejected) — matches /cfo's
    # naive `total_spend`.
    total_spend_q = await db.execute(
        _inv(
            select(func.coalesce(func.sum(Invoice.amount), 0)).where(
                Invoice.invoice_date >= period_start,
                Invoice.status != InvoiceStatus.rejected.value,
            )
        )
    )
    total_spend = Decimal(str(total_spend_q.scalar() or 0))

    # Invoice count in window (same filter as total spend).
    invoice_count_q = await db.execute(
        _inv(
            select(func.count(Invoice.id)).where(
                Invoice.invoice_date >= period_start,
                Invoice.status != InvoiceStatus.rejected.value,
            )
        )
    )
    invoice_count = int(invoice_count_q.scalar() or 0)

    # Open-payables balance — same status set as /cfo's accounts_payable_balance.
    outstanding_q = await db.execute(
        _inv(
            select(func.coalesce(func.sum(Invoice.amount), 0)).where(
                Invoice.status.in_(_OPEN_AP_STATUSES)
            )
        )
    )
    outstanding_amount = Decimal(str(outstanding_q.scalar() or 0))

    # Open-exceptions count (entity-scoped) — fails soft to 0.
    open_exceptions = 0
    try:
        from app.models.exception import Exception as APException

        exc_q = await db.execute(
            apply_entity_scope(
                select(func.count(APException.id)).where(APException.status == "open"),
                APException,
                entity_id,
            )
        )
        open_exceptions = int(exc_q.scalar() or 0)
    except Exception:  # noqa: BLE001
        open_exceptions = 0

    # Open-PO accrual — reuses /cfo's `_open_po_sum_query` (entity-scoped,
    # literal-0 on tenants without procurement tables).
    open_po_q = await db.execute(_open_po_sum_query(entity_id))
    try:
        open_po_amount = Decimal(str(open_po_q.scalar() or 0))
    except Exception:  # noqa: BLE001
        open_po_amount = Decimal("0")

    return {
        "total_spend": str(total_spend),
        "outstanding_amount": str(outstanding_amount),
        "invoice_count": invoice_count,
        "open_exceptions": open_exceptions,
        "open_po_amount": str(open_po_amount),
    }


@router.get("/by-entity")
async def get_analytics_by_entity(
    period_days: int = Query(365, ge=30, le=730),
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(*_CFO_ROLES)),
):
    """Side-by-side per-entity AP rollup PLUS a consolidated total — the
    "consolidated reporting across entities" view (multi-entity).

    Unlike every other endpoint in this file this one **ignores** the
    ``X-Entity-ID`` selection: it reports ALL active entities at once,
    each scoped row computed via ``_entity_metrics(entity_id=...)`` and a
    final ``consolidated`` block computed with ``entity_id=None`` (so it
    equals the sum across the rows and is the cross-check).

    Entities are ordered default-first, then by name — matching the entity
    switcher's order. Money is string-Decimal throughout.
    """
    from app.models.entity import Entity

    period_start = date.today() - timedelta(days=period_days)

    entities = (
        (
            await db.execute(
                select(Entity)
                .where(Entity.is_active)
                .order_by(Entity.is_default.desc(), Entity.name)
            )
        )
        .scalars()
        .all()
    )

    rows: list[dict] = []
    for e in entities:
        metrics = await _entity_metrics(db, entity_id=e.id, period_start=period_start)
        rows.append(
            {
                "entity_id": str(e.id),
                "entity_name": e.name,
                "entity_slug": e.slug,
                "currency": e.currency,
                "is_default": e.is_default,
                **metrics,
            }
        )

    consolidated = await _entity_metrics(db, entity_id=None, period_start=period_start)

    return {
        "period_days": period_days,
        "period_start": period_start.isoformat(),
        "entities": rows,
        "consolidated": consolidated,
    }


# ---------------------------------------------------------------------------
# /api/analytics/drill/spend_concentration — top vendors with invoice list
# ---------------------------------------------------------------------------


@router.get("/drill/spend_concentration")
async def drill_spend_concentration(
    period_days: int = Query(365, ge=30, le=730),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(*_CFO_ROLES)),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    """Drill-through for the supplier-concentration tile. Returns
    the top-N vendors by spend with their share, invoice count,
    and a few representative invoice IDs the CFO can click into."""
    period_start = date.today() - timedelta(days=period_days)
    reporting_currency = resolve_reporting_currency(org.settings)
    rows = await db.execute(
        apply_entity_scope(
            select(
                Invoice.vendor_name,
                Invoice.amount,
                Invoice.currency,
                Invoice.reporting_amount,
                Invoice.reporting_currency,
            ).where(
                Invoice.invoice_date >= period_start,
                Invoice.vendor_name.isnot(None),
                Invoice.vendor_name != "",
                # Same population as the concentration tile this drills into
                # (get_cfo_analytics) — rejected invoices were never real
                # spend. Without this the drill-through total disagreed with
                # the tile the CFO clicked from.
                Invoice.status != InvoiceStatus.rejected.value,
            ),
            Invoice,
            entity_id,
        )
    )
    # Rolled into the org's reporting currency (not a naive SUM across
    # currencies) — a vendor billing in more than one currency used to add
    # e.g. USD + EUR as if they were one currency.
    vendor_entries = vendor_rollup_to_reporting_currency(
        [
            {
                "vendor": vendor,
                "amount": amount,
                "currency": currency,
                "reporting_amount": rep_amt,
                "reporting_currency": rep_cur,
            }
            for vendor, amount, currency, rep_amt, rep_cur in rows.all()
        ],
        reporting_currency=reporting_currency,
    )[:limit]
    total = sum((e.amount for e in vendor_entries), Decimal("0"))
    return {
        "period_days": period_days,
        "rows": [
            {
                "vendor": e.vendor,
                "amount": float(e.amount),
                "share_pct": float((e.amount / total * Decimal("100")).quantize(Decimal("0.1")))
                if total > 0
                else 0.0,
                "invoice_count": e.invoice_count,
            }
            for e in vendor_entries
        ],
        "total_spend": float(total),
    }


# ---------------------------------------------------------------------------
# /api/analytics/drill/dpo — per-month invoice + payment volumes
# ---------------------------------------------------------------------------


@router.get("/drill/dpo")
async def drill_dpo(
    months: int = Query(12, ge=1, le=24),
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(*_CFO_ROLES)),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    """Per-month accounts-payable balance + COGS used to derive
    each DPO point. Lets the CFO see what's driving a spike."""
    today = date.today()
    cursor = today.replace(day=1)
    rows = []
    for _ in range(months):
        month_end = cursor - timedelta(days=1)
        month_start = month_end.replace(day=1)
        cogs_q = await db.execute(
            apply_entity_scope(
                select(func.coalesce(func.sum(Invoice.amount), 0)).where(
                    Invoice.invoice_date >= month_start,
                    Invoice.invoice_date <= month_end,
                ),
                Invoice,
                entity_id,
            )
        )
        feoh_q = await db.execute(
            apply_entity_scope(
                select(func.coalesce(func.sum(Invoice.amount), 0)).where(
                    Invoice.invoice_date <= month_end,
                    Invoice.status.in_(
                        [
                            InvoiceStatus.approved.value,
                            InvoiceStatus.sending_to_erp.value,
                            InvoiceStatus.sent_to_erp.value,
                            InvoiceStatus.posted_in_erp.value,
                            InvoiceStatus.payment_scheduled.value,
                        ]
                    ),
                ),
                Invoice,
                entity_id,
            )
        )
        cogs = Decimal(str(cogs_q.scalar() or 0))
        ap = Decimal(str(feoh_q.scalar() or 0))
        dpo_val = compute_dpo(accounts_payable=ap, cogs=cogs, period_days=30)
        rows.append(
            {
                "month": month_start.strftime("%Y-%m"),
                "accounts_payable": float(ap),
                "cogs": float(cogs),
                "dpo": float(dpo_val),
            }
        )
        cursor = month_start
    rows.reverse()
    return {"months": months, "rows": rows}


# ---------------------------------------------------------------------------
# /api/analytics/forecast_variance — bring-your-own forecast
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# /api/analytics/export/{report} — CSV download endpoints
# ---------------------------------------------------------------------------


@router.get("/export/{report}")
async def export_report(
    report: str,
    period_days: int = Query(90, ge=1, le=730),
    granularity: str = Query("week", pattern="^(day|week|month)$"),
    horizon_days: int = Query(90, ge=7, le=730),
    format: str = Query("csv", pattern="^(csv|pdf)$"),
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    """Analytics report download (CSV or PDF). Supported reports:

      - `invoice_register` — every invoice in the period
      - `vendor_spend` — per-vendor totals
      - `payment_register` — every payment in the period
      - `aging_snapshot` — current/1-30/31-60/61-90/90+ buckets as-of-today
      - `cashflow_forecast` — projected AP outflows per period
        (forward-looking; uses `granularity` + `horizon_days`, not
        `period_days`)
      - `expense_register` — every expense in the period

    `format` selects `csv` (default) or `pdf`. Both carry the tenant's
    white-label brand: the CSV is prefixed with a `#`-comment provenance block
    (product name + org + report + generated-at — the data grid is unchanged and
    still parses column-positionally); the PDF draws a branded header (logo when
    embeddable, else product name in the accent color). Brand resolves through
    the shared `services/branding.get_brand_context` helper.

    AP-manager + CFO can both pull these — they're operational reports, not
    privileged CFO analytics.
    """
    from app.services.branding import get_brand_context
    from app.services.report_export import EXPORTERS, brand_provenance_header

    if report not in EXPORTERS:
        raise HTTPException(
            status_code=404,
            detail=f"unknown report '{report}'; supported: {sorted(EXPORTERS)}",
        )

    period_start = date.today() - timedelta(days=period_days)
    payload: str

    if report == "cashflow_forecast":
        today = date.today()
        rows = await _commitment_rows(
            db, today=today, horizon_days=horizon_days, include_pending=True, entity_id=entity_id
        )
        periods = bucket_outflows(rows, granularity=granularity, today=today)
        payload = EXPORTERS[report](periods)
    elif report == "invoice_register":
        rows = await db.execute(
            apply_entity_scope(
                select(Invoice).where(Invoice.invoice_date >= period_start), Invoice, entity_id
            )
        )
        payload = EXPORTERS[report](rows.scalars().all())
    elif report == "vendor_spend":
        rows = await db.execute(
            apply_entity_scope(
                select(
                    Invoice.vendor_name,
                    Invoice.amount,
                    Invoice.currency,
                    Invoice.reporting_amount,
                    Invoice.reporting_currency,
                ).where(
                    Invoice.invoice_date >= period_start,
                    Invoice.vendor_name.isnot(None),
                    Invoice.vendor_name != "",
                    # Same population as the CFO concentration tile
                    # (get_cfo_analytics) and its drill-through — rejected
                    # invoices were never real spend. Without this the export
                    # disagreed with both.
                    Invoice.status != InvoiceStatus.rejected.value,
                ),
                Invoice,
                entity_id,
            )
        )
        # Rolled into the org's reporting currency (not a naive SUM across
        # currencies) — a vendor billing in more than one currency used to
        # add e.g. USD + EUR as if they were one currency.
        vendor_entries = vendor_rollup_to_reporting_currency(
            [
                {
                    "vendor": vendor,
                    "amount": amount,
                    "currency": currency,
                    "reporting_amount": rep_amt,
                    "reporting_currency": rep_cur,
                }
                for vendor, amount, currency, rep_amt, rep_cur in rows.all()
            ],
            reporting_currency=resolve_reporting_currency(org.settings if org else None),
        )
        payload = EXPORTERS[report](vendor_entries)
    elif report == "payment_register":
        rows = await db.execute(
            apply_entity_scope(
                select(Payment, Invoice)
                .outerjoin(Invoice, Invoice.id == Payment.invoice_id)
                .where(
                    Payment.created_at
                    >= datetime.combine(period_start, datetime.min.time()).replace(tzinfo=UTC)
                ),
                Payment,
                entity_id,
            )
        )
        payload = EXPORTERS[report](rows.all())
    elif report == "expense_register":
        # Joins ExpenseReport (report number) + GLAccount (GL code) with
        # outer joins so an uncoded / unattached expense still emits a row —
        # mirrors `api/expenses.py::export_expenses`' query exactly, this
        # surface just adds the branded provenance header.
        rows = await db.execute(
            apply_entity_scope(
                select(Expense, ExpenseReport.report_number, GLAccount.code)
                .outerjoin(GLAccount, GLAccount.id == Expense.gl_account_id)
                .outerjoin(ExpenseReport, ExpenseReport.id == Expense.report_id)
                .where(Expense.expense_date >= period_start),
                Expense,
                entity_id,
            )
        )
        payload = EXPORTERS[report](rows.all())
    elif report == "aging_snapshot":
        today = date.today()
        # Aging covers the same open-payable population as the AP balance so the
        # buckets sum to it (F-4): approved → payment_scheduled, not the
        # pre-approval statuses that aren't a confirmed liability yet. The AP
        # balance has no due_date filter, so this must not either — an open
        # invoice missing a due date used to inflate the balance while
        # vanishing from every bucket.
        aging_rows = await db.execute(
            apply_entity_scope(
                select(Invoice.due_date, Invoice.amount).where(
                    Invoice.status.in_(OPEN_AP_STATUSES),
                ),
                Invoice,
                entity_id,
            )
        )
        buckets = {
            "current": Decimal("0"),
            "days_30": Decimal("0"),
            "days_60": Decimal("0"),
            "days_90": Decimal("0"),
            "days_90_plus": Decimal("0"),
        }
        for due, amt in aging_rows.all():
            amount = Decimal(str(amt))
            # A null due_date can't be judged overdue — bucket as "current"
            # (the conservative read) rather than dropping it entirely.
            if due is None:
                buckets["current"] += amount
                continue
            days_past = (today - due).days
            if days_past <= 0:
                buckets["current"] += amount
            elif days_past <= 30:
                buckets["days_30"] += amount
            elif days_past <= 60:
                buckets["days_60"] += amount
            elif days_past <= 90:
                buckets["days_90"] += amount
            else:
                buckets["days_90_plus"] += amount
        payload = EXPORTERS[report](buckets)
    else:
        # Unreachable in practice — every key in EXPORTERS has a branch above,
        # and `report not in EXPORTERS` already 404'd earlier in this
        # function. This is a hard guard against exactly the bug this
        # replaces: a new EXPORTERS entry with no matching branch here used
        # to silently fall through to the aging_snapshot path instead of
        # failing loudly.
        raise HTTPException(
            status_code=500,
            detail=f"report '{report}' is registered in EXPORTERS but has no dispatch branch",
        )

    brand = get_brand_context(org.settings if org else None)
    generated_at = datetime.now(UTC)
    period_label = (
        f"forward {horizon_days} days ({granularity})"
        if report == "cashflow_forecast"
        else f"trailing {period_days} days (since {period_start.isoformat()})"
    )

    if format == "pdf":
        import csv as _csv
        import io as _io

        from app.services.analytics_report_pdf import (
            AnalyticsReportContext,
            render_analytics_report_pdf,
        )

        # Re-parse the CSV the exporter produced into header + rows so the PDF
        # renders EXACTLY the same cells the CSV dialect emits — never broader.
        parsed = list(_csv.reader(_io.StringIO(payload)))
        header_row = parsed[0] if parsed else []
        data_rows = parsed[1:] if len(parsed) > 1 else []
        ctx = AnalyticsReportContext(
            title=report.replace("_", " ").title(),
            org_name=(org.name if org else "Organization"),
            period_label=period_label,
            generated_at=generated_at,
            header=header_row,
            rows=data_rows,
            brand=brand,
        )
        pdf_bytes = render_analytics_report_pdf(ctx)
        filename = f"{report}_{date.today().isoformat()}.pdf"
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # CSV: prepend the brand provenance comment block, then the data grid.
    branded_csv = (
        brand_provenance_header(
            brand,
            org_name=(org.name if org else None),
            report=report,
            generated_at=generated_at,
        )
        + payload
    )
    filename = f"{report}_{date.today().isoformat()}.csv"
    return Response(
        content=branded_csv,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/forecast_variance")
async def post_forecast_variance(
    body: dict,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(*_CFO_ROLES)),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    """The org POSTs `{"months": [{"month": "YYYY-MM",
    "forecast": "100000"}, ...]}` and we return the same list with
    the actual outflow per month + the variance + variance_pct.

    Forecasts are not persisted — the CFO either pastes from their
    FP&A tool or re-runs the call with adjusted numbers. The
    actuals-vs-forecast comparison is what we contribute."""
    rows = body.get("months") or []
    if not isinstance(rows, list):
        raise HTTPException(status_code=400, detail="`months` must be a list")
    augmented: list[dict] = []
    for r in rows:
        month = r.get("month")
        if not isinstance(month, str) or len(month) != 7:
            raise HTTPException(status_code=400, detail="each row needs `month` in YYYY-MM format")
        try:
            year, mon = (int(x) for x in month.split("-"))
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=f"bad month value: {month!r}") from exc
        start = date(year, mon, 1)
        end = date(year + (mon // 12), (mon % 12) + 1, 1) - timedelta(days=1)
        actual_q = await db.execute(
            apply_entity_scope(
                select(func.coalesce(func.sum(Payment.amount), 0)).where(
                    Payment.status == "completed",
                    Payment.completed_at
                    >= datetime.combine(start, datetime.min.time()).replace(tzinfo=UTC),
                    Payment.completed_at
                    <= datetime.combine(end, datetime.max.time()).replace(tzinfo=UTC),
                ),
                Payment,
                entity_id,
            )
        )
        augmented.append(
            {
                "month": month,
                "forecast": r.get("forecast", "0"),
                "actual": str(actual_q.scalar() or 0),
            }
        )
    result = compute_forecast_variance(augmented)
    return {
        "rows": [
            {
                "month": r["month"],
                "forecast": float(r["forecast"]),
                "actual": float(r["actual"]),
                "variance": float(r["variance"]),
                "variance_pct": float(r["variance_pct"]),
            }
            for r in result
        ]
    }
