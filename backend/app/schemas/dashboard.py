"""Response schema for ``GET /api/dashboard``.

Money fields use the ``MoneyAmount``/``OptionalMoneyAmount`` annotations
(Decimal in Python, JSON number on the wire) — see ``app/schemas/money.py``.
The endpoint itself keeps every currency figure as ``Decimal`` right up to
the response; this schema is what performs the single float hop, at
JSON-serialization time, matching the pattern used by
``app/schemas/discount.py`` and friends. Day-count / percentage fields
(``processing_time``, ``approval_bottleneck``, ``touchless_rate``,
``capture_rate_pct``) are not currency and stay plain ``float``.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.schemas.money import MoneyAmount


class ReportingByCurrency(BaseModel):
    currency: str
    original_amount: MoneyAmount
    reporting_amount: MoneyAmount
    count: int
    unconverted_count: int


class ReportingRollup(BaseModel):
    reporting_currency: str
    total_amount: MoneyAmount
    total_count: int
    unconverted_count: int
    by_currency: list[ReportingByCurrency]


class VendorSpendEntry(BaseModel):
    vendor: str
    amount: MoneyAmount


class AgingBuckets(BaseModel):
    current: MoneyAmount
    days_30: MoneyAmount
    days_60: MoneyAmount
    days_90: MoneyAmount
    days_90_plus: MoneyAmount


class MonthlyTrendEntry(BaseModel):
    month: str
    count: int
    amount: MoneyAmount
    reporting_amount: MoneyAmount


class UpcomingPayment(BaseModel):
    id: str
    invoice_number: str | None
    vendor_name: str | None
    amount: MoneyAmount
    due_date: str | None
    is_overdue: bool


class ProcessingTimeMetrics(BaseModel):
    """Time-to-X distributions in business days — not money, stays float."""

    avg_upload_to_approval_days: float
    median_upload_to_approval_days: float
    p95_upload_to_approval_days: float
    avg_upload_to_paid_days: float
    median_upload_to_paid_days: float
    p95_upload_to_paid_days: float
    count_approval_leg: int
    count_paid_leg: int


class ApprovalBottleneckEntry(BaseModel):
    """Per-approver pending-approval stats — day counts, not money."""

    approver_id: str
    approver_name: str | None
    pending_count: int
    oldest_pending_days: float
    avg_pending_days: float


class DiscountCapture(BaseModel):
    eligible_count: int
    captured_count: int
    missed_count: int
    captured_amount: MoneyAmount
    missed_amount: MoneyAmount
    capture_rate_pct: float


class DashboardResponse(BaseModel):
    total_invoices: int
    total_amount: MoneyAmount
    reporting: ReportingRollup
    total_paid: MoneyAmount
    total_pending: MoneyAmount
    # Reporting-currency counterparts of `total_paid` / `total_pending` — see
    # `app/api/dashboard.py`'s `payment_reporting_amount_sql` comment.
    total_paid_reporting: MoneyAmount
    total_pending_reporting: MoneyAmount
    total_paid_unconverted_count: int
    total_pending_unconverted_count: int
    total_rebates: MoneyAmount
    open_exceptions: int
    touchless_rate: float
    stale_approvals: int
    pipeline: dict[str, int]
    vendor_spend: list[VendorSpendEntry]
    aging: AgingBuckets
    aging_reporting: AgingBuckets
    monthly_trend: list[MonthlyTrendEntry]
    upcoming_payments: list[UpcomingPayment]
    upcoming_total_amount: MoneyAmount
    upcoming_total_amount_reporting: MoneyAmount
    upcoming_unconverted_count: int
    processing_time: ProcessingTimeMetrics
    approval_bottleneck: list[ApprovalBottleneckEntry]
    discount_capture: DiscountCapture
