"""Cash-flow copilot tools — forecast, running position, payment-timing
what-if, and the Phase 2 proposed-plan action.

Finance-leader-only reads (``ToolSpec.allowed_roles`` — enforced by the
orchestrator's ``run_tool``). Every figure comes from the pure functions in
``services.analytics`` over the same commitment rows the CFO dashboard uses
(``app/api/analytics.py::_commitment_rows``); money stays ``Decimal`` end to
end and serializes as an exact JSON string — these tools deliberately do NOT
inherit the analytics HTTP endpoints' ``float()`` chart coercion. The LLM
never computes a number. See ``docs/cash-flow-copilot.md``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.analytics import _commitment_rows
from app.api.discounts import _name_maps
from app.config import settings
from app.models.organization import Organization
from app.services.analytics import (
    apply_payment_timing_scenario,
    bucket_outflows,
    compute_cash_position,
)
from app.services.assistant.tools._currency import resolve_org_currency
from app.services.assistant.tools.optimizer import (
    build_discount_recommendations,
    run_discount_optimization,
)
from app.services.assistant.tools.schemas import (
    CashflowForecastParams,
    CashflowForecastResult,
    CashflowPeriod,
    CashPositionParams,
    CashPositionPeriod,
    CashPositionResult,
    PaymentPlanPeriod,
    PaymentPlanResult,
    PaymentWhatifParams,
    PaymentWhatifResult,
    ProposePaymentPlanParams,
    WhatifScenario,
)
from app.services.cash_flow_plan import assemble_plan
from app.services.cashflow import fetch_provider_balance, resolve_cash_thresholds

_ZERO = Decimal("0")


def _horizon(days: int | None) -> int:
    return days if days is not None else settings.cashflow_copilot_default_horizon_days


async def _resolve_opening_balance(
    *,
    control_db: AsyncSession | None,
    org_id: uuid.UUID,
    explicit_opening: Decimal | None,
    explicit_threshold: Decimal | None,
) -> tuple[Decimal, str, Decimal | None]:
    """Opening-balance + threshold resolution: explicit param → provider
    auto-sync → persisted org settings → 0. Mirrors ``GET
    /api/analytics/cash_position``. Shared by ``get_cash_position`` and
    ``propose_payment_plan`` so the two can never resolve a different opening
    balance for the same org."""
    org_settings: dict = {}
    if control_db is not None:
        org = await control_db.get(Organization, org_id)
        org_settings = (org.settings or {}) if org else {}

    opening = explicit_opening
    source = "explicit"
    if opening is None and (payments_config := org_settings.get("payments")):
        provider_balance = await fetch_provider_balance(payments_config)
        if provider_balance is not None:
            opening = provider_balance.amount
            source = "provider"
    if opening is None:
        settings_balance = (org_settings.get("cashflow") or {}).get("opening_balance")
        if settings_balance is not None:
            opening = Decimal(str(settings_balance))
            source = "settings"
    if opening is None:
        opening = _ZERO
        source = "none"

    threshold = explicit_threshold
    if threshold is None:
        threshold = resolve_cash_thresholds(org_settings).min_balance_threshold

    return opening, source, threshold


async def get_cashflow_forecast(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    entity_id: uuid.UUID | None,
    current_user_id: uuid.UUID,
    params: CashflowForecastParams,
    control_db: AsyncSession | None = None,
) -> CashflowForecastResult:
    today = datetime.now(UTC).date()
    horizon_days = _horizon(params.horizon_days)
    rows = await _commitment_rows(
        db,
        today=today,
        horizon_days=horizon_days,
        include_pending=params.include_pending,
        entity_id=entity_id,
    )
    buckets = bucket_outflows(rows, granularity=params.granularity, today=today)

    periods: list[CashflowPeriod] = []
    total_scheduled = total_committed = total_pending = _ZERO
    for b in buckets:
        scheduled = Decimal(str(b["scheduled_amount"]))
        committed = Decimal(str(b["committed_amount"]))
        pending = Decimal(str(b["pending_amount"]))
        total_scheduled += scheduled
        total_committed += committed
        total_pending += pending
        periods.append(
            CashflowPeriod(
                period=b["period"],
                scheduled=scheduled,
                committed=committed,
                pending=pending,
                discount_eligible=Decimal(str(b["discount_eligible_amount"])),
                count=int(b["count"]),
            )
        )

    return CashflowForecastResult(
        currency=await resolve_org_currency(org_id, control_db),
        granularity=params.granularity,
        horizon_days=horizon_days,
        periods=periods,
        total_scheduled=total_scheduled,
        total_committed=total_committed,
        total_pending=total_pending,
    )


async def get_cash_position(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    entity_id: uuid.UUID | None,
    current_user_id: uuid.UUID,
    params: CashPositionParams,
    control_db: AsyncSession | None = None,
) -> CashPositionResult:
    today = datetime.now(UTC).date()
    horizon_days = _horizon(params.horizon_days)
    rows = await _commitment_rows(
        db, today=today, horizon_days=horizon_days, include_pending=True, entity_id=entity_id
    )
    outflow_periods = bucket_outflows(rows, granularity=params.granularity, today=today)

    # The source is surfaced so the copilot can say where the number came from.
    opening, source, threshold = await _resolve_opening_balance(
        control_db=control_db,
        org_id=org_id,
        explicit_opening=params.opening_balance,
        explicit_threshold=params.min_balance_threshold,
    )

    position = compute_cash_position(opening, outflow_periods, min_balance_threshold=threshold)

    periods: list[CashPositionPeriod] = []
    first_shortfall: str | None = None
    for p in position:
        below = bool(p["below_threshold"])
        if below and first_shortfall is None:
            first_shortfall = p["period"]
        periods.append(
            CashPositionPeriod(
                period=p["period"],
                opening=Decimal(str(p["opening"])),
                outflow=Decimal(str(p["outflow"])),
                closing=Decimal(str(p["closing"])),
                below_threshold=below,
            )
        )

    return CashPositionResult(
        currency=await resolve_org_currency(org_id, control_db),
        granularity=params.granularity,
        horizon_days=horizon_days,
        opening_balance=opening,
        opening_balance_source=source,
        min_balance_threshold=threshold,
        periods=periods,
        first_shortfall_period=first_shortfall,
    )


async def run_payment_whatif(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    entity_id: uuid.UUID | None,
    current_user_id: uuid.UUID,
    params: PaymentWhatifParams,
    control_db: AsyncSession | None = None,
) -> PaymentWhatifResult:
    today = datetime.now(UTC).date()
    horizon_days = _horizon(params.horizon_days)
    rows = await _commitment_rows(
        db, today=today, horizon_days=horizon_days, include_pending=True, entity_id=entity_id
    )

    scenarios: list[WhatifScenario] = []
    for name in ("early", "on_time", "late"):
        s = apply_payment_timing_scenario(
            rows,
            scenario=name,
            granularity=params.granularity,
            grace_days=params.grace_days,
            today=today,
        )
        scenarios.append(
            WhatifScenario(
                scenario=name,
                total_outflow=Decimal(str(s["total_outflow"])),
                discount_captured=Decimal(str(s["total_discount_captured"])),
                weighted_avg_days_to_pay=Decimal(str(s["weighted_avg_pay_date_days"])),
            )
        )

    return PaymentWhatifResult(
        currency=await resolve_org_currency(org_id, control_db),
        horizon_days=horizon_days,
        grace_days=params.grace_days,
        scenarios=scenarios,
    )


async def propose_payment_plan(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    entity_id: uuid.UUID | None,
    current_user_id: uuid.UUID,
    params: ProposePaymentPlanParams,
    control_db: AsyncSession | None = None,
) -> PaymentPlanResult:
    """Phase 2 — assemble a proposed payment plan: which open commitments to
    pay when, which discount offers to capture, and the resulting
    cash-position curve. Read-only; never mutates an invoice/payment/offer —
    see ``services.cash_flow_plan.assemble_plan`` and
    ``docs/cash-flow-copilot.md`` §5 for the safety model. The discount
    selection is the exact same pass ``optimize_discount_capture`` and ``POST
    /api/discounts/optimize`` run (``run_discount_optimization``), so the
    plan can never recommend a different capture than the discounts
    dashboard would for equivalent inputs."""
    today = datetime.now(UTC).date()
    horizon_days = _horizon(params.horizon_days)
    rows = await _commitment_rows(
        db, today=today, horizon_days=horizon_days, include_pending=True, entity_id=entity_id
    )

    opening, source, threshold = await _resolve_opening_balance(
        control_db=control_db,
        org_id=org_id,
        explicit_opening=params.opening_balance,
        explicit_threshold=params.min_balance_threshold,
    )

    optimizer_result, offers = await run_discount_optimization(
        db,
        org_id=org_id,
        entity_id=entity_id,
        control_db=control_db,
        cash_budget=params.cash_budget,
        cost_of_capital_pct=params.cost_of_capital_pct,
        today=today,
    )
    vmap, imap = await _name_maps(db, offers)
    recommendations = build_discount_recommendations(optimizer_result, offers, vmap, imap)

    plan = assemble_plan(
        rows,
        optimizer_result=optimizer_result,
        opening_balance=opening,
        min_balance_threshold=threshold,
        granularity=params.granularity,
        horizon_days=horizon_days,
        today=today,
    )

    return PaymentPlanResult(
        currency=await resolve_org_currency(org_id, control_db),
        granularity=params.granularity,
        horizon_days=horizon_days,
        opening_balance=plan.opening_balance,
        opening_balance_source=source,
        min_balance_threshold=threshold,
        periods=[
            PaymentPlanPeriod(
                period=p.period,
                opening=p.opening,
                outflow=p.outflow,
                closing=p.closing,
                below_threshold=p.below_threshold,
            )
            for p in plan.periods
        ],
        first_shortfall_period=plan.first_shortfall_period,
        cost_of_capital_pct=optimizer_result.cost_of_capital_pct,
        total_savings_selected=plan.total_savings_selected,
        total_outlay_selected=plan.total_outlay_selected,
        discount_recommendations=recommendations,
        unretimed_offer_ids=plan.unretimed_offer_ids,
    )
