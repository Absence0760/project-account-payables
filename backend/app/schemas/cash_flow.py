"""Request / response schemas for the AI Cash-Flow Copilot's Phase 3 enact
routes (`app/api/cash_flow.py`). See docs/cash-flow-copilot.md §5/§6."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class CashFlowPlanReplay(BaseModel):
    """The plan-defining parameters read off the `propose_payment_plan` tool
    result that produced the plan being enacted (`PaymentPlanResult`'s own
    `granularity` / `horizon_days` / `min_balance_threshold` / `cash_budget` /
    `cost_of_capital_pct` fields — already RESOLVED values, not the original
    possibly-`None` request overrides).

    NEVER trusted for WHAT to act on — the server re-derives its own
    commitment rows / discount-offer selection from these inputs. It is used
    ONLY to recompute the plan's `plan_id` and confirm it still matches the
    id in the URL (the stale-plan guard — see
    `services.cash_flow_plan.compute_plan_id`). Mirrors
    `ProposePaymentPlanParams` minus `opening_balance`, which only affects the
    displayed cash-position curve — never which invoices are due or which
    discounts are worth capturing — so it plays no part in either enact
    action.
    """

    granularity: Literal["day", "week", "month"] = "week"
    horizon_days: int | None = Field(default=None, ge=7, le=730)
    min_balance_threshold: Decimal | None = None
    cash_budget: Decimal | None = Field(default=None, ge=0)
    cost_of_capital_pct: Decimal | None = Field(default=None, ge=0, le=100)


class DraftRunResponse(BaseModel):
    plan_id: str
    # False when this call returned an ALREADY-EXISTING draft run for this
    # plan_id (idempotent replay) rather than staging a new one.
    created: bool
    run_id: uuid.UUID
    status: str
    total_amount: Decimal
    payment_count: int
    requires_cfo_approval: bool
    # A payment run is single-currency (`PaymentRun.total_amount` is a bare
    # Numeric and the CFO threshold is compared against it as a bare number),
    # so a plan spanning several currencies stages the ORG's reporting-currency
    # slice and says what it left behind. `excluded_currency_count` is 0 for
    # every single-currency tenant.
    run_currency: str = "USD"
    excluded_currency_count: int = 0


class CaptureDiscountsResponse(BaseModel):
    plan_id: str
    accepted_offer_ids: list[str]
    accepted_count: int
    # Offers the SAME optimizer pass selected but that were skipped because
    # they were no longer `offered` (already accepted/captured/declined/
    # expired — e.g. a prior call, or a manual accept via /discounts, already
    # handled them) or their tier window closed between the optimizer pass
    # and now. Never an error — status-only idempotency.
    skipped_count: int
    total_savings_selected: Decimal
