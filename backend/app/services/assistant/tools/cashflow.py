"""Cash-flow copilot tools — forecast, running position, payment-timing what-if.

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
from app.config import settings
from app.models.organization import Organization
from app.services.analytics import (
    apply_payment_timing_scenario,
    bucket_outflows,
    compute_cash_position,
)
from app.services.assistant.tools._currency import resolve_org_currency
from app.services.assistant.tools.schemas import (
    CashflowForecastParams,
    CashflowForecastResult,
    CashflowPeriod,
    CashPositionParams,
    CashPositionPeriod,
    CashPositionResult,
    PaymentWhatifParams,
    PaymentWhatifResult,
    WhatifScenario,
)
from app.services.cashflow import fetch_provider_balance, resolve_cash_thresholds

_ZERO = Decimal("0")


def _horizon(days: int | None) -> int:
    return days if days is not None else settings.cashflow_copilot_default_horizon_days


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

    # Opening-balance resolution mirrors GET /api/analytics/cash_position:
    # explicit param → provider auto-sync → persisted org settings → 0. The
    # source is surfaced so the copilot can say where the number came from.
    org_settings: dict = {}
    if control_db is not None:
        org = await control_db.get(Organization, org_id)
        org_settings = (org.settings or {}) if org else {}

    opening = params.opening_balance
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

    threshold = params.min_balance_threshold
    if threshold is None:
        threshold = resolve_cash_thresholds(org_settings).min_balance_threshold

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
