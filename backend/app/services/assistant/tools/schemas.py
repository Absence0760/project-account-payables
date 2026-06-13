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
