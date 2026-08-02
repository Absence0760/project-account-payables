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


class CashflowForecastResult(BaseModel):
    currency: str
    granularity: str
    horizon_days: int
    periods: list[CashflowPeriod]
    total_scheduled: Decimal
    total_committed: Decimal
    total_pending: Decimal


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


class CashPositionResult(BaseModel):
    currency: str
    granularity: str
    horizon_days: int
    opening_balance: Decimal
    # Which link of the resolution chain supplied the balance —
    # "explicit" (caller) | "provider" (bank sync) | "settings" | "none".
    opening_balance_source: str
    min_balance_threshold: Decimal | None
    periods: list[CashPositionPeriod]
    first_shortfall_period: str | None


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
    currency: str
    granularity: str
    horizon_days: int
    opening_balance: Decimal
    opening_balance_source: str
    min_balance_threshold: Decimal | None
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
