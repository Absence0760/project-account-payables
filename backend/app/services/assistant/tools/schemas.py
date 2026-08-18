"""Pydantic param + return models for the five fixed assistant tools.

Money is always ``Decimal`` (never ``float``); on transport each ReturnModel is
dumped with ``model_dump(mode="json")``, which serialises ``Decimal`` to a
string — never a lossy float. Param models clamp limits so a leaked/odd model
arg can't request an unbounded scan.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from app.models.invoice import InvoiceStatus

# ---------------------------------------------------------------------------
# list_invoices
# ---------------------------------------------------------------------------


class ListInvoicesParams(BaseModel):
    status: list[InvoiceStatus] | None = None
    vendor_name: str | None = None  # ILIKE-contains, resolved to vendor_ids in-tool
    date_from: date | None = None
    date_to: date | None = None
    amount_min: Decimal | None = None
    amount_max: Decimal | None = None
    limit: int = Field(default=20, ge=1, le=50)
    offset: int = Field(default=0, ge=0)


class InvoiceSummary(BaseModel):
    id: str
    invoice_number: str
    vendor_name: str
    amount: Decimal
    currency: str
    status: str
    invoice_date: date | None = None
    due_date: date | None = None


class InvoiceListResult(BaseModel):
    items: list[InvoiceSummary]
    total: int
    applied_filters: dict


# ---------------------------------------------------------------------------
# get_vendor_spend
# ---------------------------------------------------------------------------


class VendorSpendParams(BaseModel):
    period: Literal["mtd", "qtd", "ytd", "last_30d", "last_90d", "last_12m"] = "ytd"
    top_n: int = Field(default=10, ge=1, le=25)


class VendorSpendRow(BaseModel):
    vendor_id: str | None = None
    vendor_name: str
    amount: Decimal
    share_pct: Decimal


class VendorSpendResult(BaseModel):
    period_label: str
    currency: str
    total_spend: Decimal
    vendors: list[VendorSpendRow]


# ---------------------------------------------------------------------------
# list_pending_approvals
# ---------------------------------------------------------------------------


class PendingApprovalsParams(BaseModel):
    assignee: Literal["me", "anyone"] = "me"
    limit: int = Field(default=20, ge=1, le=50)


class PendingApprovalRow(BaseModel):
    invoice_id: str
    invoice_number: str
    vendor_name: str
    amount: Decimal
    currency: str
    assigned_to_id: str | None = None
    waiting_since: date | None = None


class PendingApprovalsResult(BaseModel):
    items: list[PendingApprovalRow]
    total: int


# ---------------------------------------------------------------------------
# get_payment_forecast
# ---------------------------------------------------------------------------


class ForecastParams(BaseModel):
    horizon: Literal["7d", "14d", "30d", "60d", "90d"] = "30d"
    granularity: Literal["day", "week", "month"] = "week"


class ForecastBucket(BaseModel):
    period: str
    amount: Decimal
    count: int


class ForecastResult(BaseModel):
    currency: str
    horizon_label: str
    buckets: list[ForecastBucket]
    total: Decimal


# ---------------------------------------------------------------------------
# find_invoices_by_text
# ---------------------------------------------------------------------------


class TextSearchParams(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    k: int = Field(default=5, ge=1, le=15)


class TextSearchMatch(BaseModel):
    invoice_id: str
    vendor_name: str | None = None
    similarity: float
    snippet: str


class TextSearchResult(BaseModel):
    matches: list[TextSearchMatch]


# ---------------------------------------------------------------------------
# Cash-flow copilot tools (finance-leader only — see ToolSpec.allowed_roles)
# ---------------------------------------------------------------------------


class CashflowForecastParams(BaseModel):
    granularity: Literal["day", "week", "month"] = "week"
    # None → FEOH_CASHFLOW_COPILOT_DEFAULT_HORIZON_DAYS (resolved in the tool).
    horizon_days: int | None = Field(default=None, ge=7, le=730)
    include_pending: bool = True


class CashflowPeriod(BaseModel):
    period: str
    scheduled: Decimal
    committed: Decimal
    pending: Decimal
    discount_eligible: Decimal
    count: int
    # Rows in this period whose reporting-currency figure could not be
    # established, so they were counted at FACE VALUE in another currency.
    # See `services.analytics.bucket_outflows`.
    unconverted_count: int = 0


class CashflowForecastResult(BaseModel):
    currency: str
    granularity: str
    horizon_days: int
    periods: list[CashflowPeriod]
    total_scheduled: Decimal
    total_committed: Decimal
    total_pending: Decimal
    # Non-zero means the totals above mix currencies — the copilot must say so
    # rather than answering with a number nobody can act on.
    unconverted_count: int = 0


class CashPositionParams(BaseModel):
    granularity: Literal["day", "week", "month"] = "week"
    horizon_days: int | None = Field(default=None, ge=7, le=730)
    opening_balance: Decimal | None = None
    min_balance_threshold: Decimal | None = None


class CashPositionPeriod(BaseModel):
    period: str
    opening: Decimal
    outflow: Decimal
    closing: Decimal
    below_threshold: bool
    unconverted_count: int = 0


class CashPositionResult(BaseModel):
    currency: str
    granularity: str
    horizon_days: int
    opening_balance: Decimal
    # Provenance of the figure the whole curve is built on — see
    # `services.cashflow.OpeningBalance`. Which link of the resolution chain
    # supplied it ("explicit" | "provider" | "settings" | "none"), and, when a
    # bank sync supplied it, which provider + which (opaque) account label so
    # the copilot can say "from your Modern Treasury operating account"
    # instead of an unattributable number.
    opening_balance_source: str
    opening_balance_provider: str | None = None
    opening_balance_account_ref: str | None = None
    # "currency_mismatch" when a live provider balance existed but was refused
    # because its account is in another currency than the org reports in.
    opening_balance_provider_skipped: str | None = None
    min_balance_threshold: Decimal | None
    periods: list[CashPositionPeriod]
    first_shortfall_period: str | None
    # The OUTFLOW-side twin of `opening_balance_provider_skipped`: commitments
    # folded into the curve at face value in a currency we could not convert.
    # The balance carries forward, so one such row poisons every later period.
    unconverted_count: int = 0


class PaymentWhatifParams(BaseModel):
    granularity: Literal["day", "week", "month"] = "week"
    horizon_days: int | None = Field(default=None, ge=7, le=730)
    grace_days: int = Field(default=15, ge=0, le=90)


class WhatifScenario(BaseModel):
    scenario: str
    total_outflow: Decimal
    discount_captured: Decimal
    weighted_avg_days_to_pay: Decimal


class PaymentWhatifResult(BaseModel):
    currency: str
    horizon_days: int
    grace_days: int
    scenarios: list[WhatifScenario]
    # A property of the commitment set, not of a scenario: rows included at
    # face value in a currency we could not convert. Comparing two scenarios'
    # outflows only means something at 0.
    unconverted_count: int = 0


class OptimizeDiscountsParams(BaseModel):
    cash_budget: Decimal | None = Field(default=None, ge=0)
    cost_of_capital_pct: Decimal | None = Field(default=None, ge=0, le=100)


class DiscountRecommendation(BaseModel):
    offer_id: str
    vendor_name: str | None
    invoice_number: str | None
    base_amount: Decimal
    discount_percent: Decimal
    annualized_return_pct: Decimal
    savings: Decimal
    pay_by: str
    selected: bool


class OptimizeDiscountsResult(BaseModel):
    currency: str
    cost_of_capital_pct: Decimal
    total_savings_available: Decimal
    total_savings_selected: Decimal
    total_outlay_selected: Decimal
    recommendations: list[DiscountRecommendation]


# ---------------------------------------------------------------------------
# propose_payment_plan (finance-leader only) — Phase 2, advisory + draft-only.
# Combines get_cash_position + optimize_discount_capture into one proposed
# plan artifact. Never moves money; see docs/cash-flow-copilot.md §5.
# ---------------------------------------------------------------------------


class ProposePaymentPlanParams(BaseModel):
    granularity: Literal["day", "week", "month"] = "week"
    horizon_days: int | None = Field(default=None, ge=7, le=730)
    opening_balance: Decimal | None = None
    min_balance_threshold: Decimal | None = None
    cash_budget: Decimal | None = Field(default=None, ge=0)
    cost_of_capital_pct: Decimal | None = Field(default=None, ge=0, le=100)


class PaymentPlanPeriod(BaseModel):
    period: str
    opening: Decimal
    outflow: Decimal
    closing: Decimal
    below_threshold: bool


class PaymentPlanResult(BaseModel):
    # Deterministic correlation key (services/cash_flow_plan.compute_plan_id)
    # over this plan's own RESOLVED defining inputs + today's date — Phase 3
    # enactment's idempotency anchor. See docs/cash-flow-copilot.md §5/§6.
    plan_id: str
    currency: str
    granularity: str
    horizon_days: int
    opening_balance: Decimal
    # Same provenance fields as `CashPositionResult` — the plan's whole cash
    # curve starts from this figure, so the reader needs to see its origin
    # before acting on a proposed pay schedule.
    opening_balance_source: str
    opening_balance_provider: str | None = None
    opening_balance_account_ref: str | None = None
    opening_balance_provider_skipped: str | None = None
    min_balance_threshold: Decimal | None
    # The raw cash-budget input the optimizer selection was run under (may be
    # None — unconstrained). Echoed back verbatim (not resolved to anything)
    # so the Phase 3 enact endpoints can recompute the SAME `plan_id` from the
    # client's replayed params without the frontend having to remember what
    # it originally sent.
    cash_budget: Decimal | None
    periods: list[PaymentPlanPeriod]
    first_shortfall_period: str | None
    cost_of_capital_pct: Decimal
    total_savings_selected: Decimal
    total_outlay_selected: Decimal
    discount_recommendations: list[DiscountRecommendation]
    # offer_ids the optimizer selected but this plan could not re-time onto
    # the cash curve (a vendor-scoped offer with no single invoice, or an
    # invoice outside the forecast horizon) — still counted in the totals
    # above, just not reflected in `periods`. See services/cash_flow_plan.py.
    unretimed_offer_ids: list[str]
