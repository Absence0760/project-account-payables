"""Pydantic schemas for dynamic discounting & early-payment optimization.

Shared contract for the ``/api/discounts`` router and the frontend. Money
fields use the ``MoneyAmount`` annotation (Decimal in Python, JSON number on
the wire); tier/ROI percents stay ``Decimal`` for exactness. See
``backend/docs/dynamic-discounting.md``.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field, PlainSerializer

from app.schemas.money import MoneyAmount, OptionalMoneyAmount


def _decimal_to_number(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


# A non-money Decimal (a percentage / rate) that serialises to a JSON *number*,
# matching the frontend's `number`-typed contract while staying exact in Python.
PercentNumber = Annotated[
    Decimal,
    PlainSerializer(_decimal_to_number, return_type=float, when_used="json"),
]


class OfferScope(StrEnum):
    invoice = "invoice"
    vendor = "vendor"


class OfferSource(StrEnum):
    supplier = "supplier"
    system = "system"
    financing = "financing"


class OfferStatus(StrEnum):
    offered = "offered"
    accepted = "accepted"
    captured = "captured"
    declined = "declined"
    expired = "expired"


class DiscountTier(BaseModel):
    """One rung of a sliding-scale offer: pay within `days` for `percent` off."""

    days: int = Field(..., ge=0, le=365)
    percent: PercentNumber = Field(..., gt=0, lt=100)


class DiscountOfferCreate(BaseModel):
    scope: OfferScope = OfferScope.invoice
    invoice_id: str | None = None
    vendor_id: str | None = None
    source: OfferSource = OfferSource.supplier
    tiers: list[DiscountTier] = Field(..., min_length=1)
    base_amount: Decimal | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    valid_from: date | None = None
    valid_until: date | None = None
    notes: str | None = Field(default=None, max_length=500)


class AcceptOfferRequest(BaseModel):
    """Accept an offer at a specific tier (defaults to the best one for today)."""

    tier_days: int | None = Field(default=None, ge=0, le=365)


class DiscountOfferResponse(BaseModel):
    id: str
    scope: str
    invoice_id: str | None
    vendor_id: str | None
    source: str
    status: str
    tiers: list[DiscountTier]
    base_amount: MoneyAmount
    currency: str
    valid_from: str | None
    valid_until: str | None
    accepted_tier: DiscountTier | None
    accepted_at: str | None
    captured_amount: OptionalMoneyAmount = None
    captured_at: str | None
    financing_provider: str | None
    notes: str | None
    created_at: str
    updated_at: str | None
    # Joined / derived (best-effort, set by the router when context is loaded).
    vendor_name: str | None = None
    invoice_number: str | None = None

    model_config = {"from_attributes": True}

    @classmethod
    def from_db(cls, o, *, vendor_name: str | None = None, invoice_number: str | None = None):
        accepted = None
        if o.accepted_tier:
            accepted = DiscountTier(
                days=int(o.accepted_tier["days"]), percent=Decimal(str(o.accepted_tier["percent"]))
            )
        return cls(
            id=str(o.id),
            scope=o.scope,
            invoice_id=str(o.invoice_id) if o.invoice_id else None,
            vendor_id=str(o.vendor_id) if o.vendor_id else None,
            source=o.source,
            status=o.status,
            tiers=[
                DiscountTier(days=int(t["days"]), percent=Decimal(str(t["percent"])))
                for t in (o.tiers or [])
            ],
            base_amount=o.base_amount,
            currency=o.currency,
            valid_from=o.valid_from.isoformat() if o.valid_from else None,
            valid_until=o.valid_until.isoformat() if o.valid_until else None,
            accepted_tier=accepted,
            accepted_at=o.accepted_at.isoformat() if o.accepted_at else None,
            captured_amount=o.captured_amount,
            captured_at=o.captured_at.isoformat() if o.captured_at else None,
            financing_provider=o.financing_provider,
            notes=o.notes,
            created_at=o.created_at.isoformat() if o.created_at else "",
            updated_at=o.updated_at.isoformat() if o.updated_at else None,
            vendor_name=vendor_name,
            invoice_number=invoice_number,
        )


class DiscountOfferListResponse(BaseModel):
    items: list[DiscountOfferResponse]
    total: int
    page: int
    page_size: int


class DiscountROIResponse(BaseModel):
    """Annualized-return analysis for one early-payment opportunity."""

    base_amount: MoneyAmount
    discount_percent: PercentNumber
    days_accelerated: int
    savings: MoneyAmount
    annualized_return_pct: PercentNumber
    cost_of_capital_pct: PercentNumber
    opportunity_cost: MoneyAmount
    net_benefit: MoneyAmount
    worthwhile: bool


class OptimizerRecommendation(BaseModel):
    """One ranked early-payment recommendation from the optimizer."""

    offer_id: str | None = None
    invoice_id: str | None = None
    vendor_id: str | None = None
    vendor_name: str | None = None
    invoice_number: str | None = None
    tier_days: int
    discount_percent: PercentNumber
    pay_by: str  # ISO date — capture deadline
    roi: DiscountROIResponse
    selected: bool  # True if it fits within the cash budget
    cumulative_outlay: MoneyAmount  # running cash committed through this rank


class OptimizerResponse(BaseModel):
    cash_budget: OptionalMoneyAmount = None
    cost_of_capital_pct: PercentNumber
    total_savings_available: MoneyAmount
    total_savings_selected: MoneyAmount
    total_outlay_selected: MoneyAmount
    recommendations: list[OptimizerRecommendation]


class BulkNegotiationRequest(BaseModel):
    """Propose a single early-pay discount across a vendor's open invoices."""

    vendor_id: str
    tiers: list[DiscountTier] = Field(..., min_length=1)
    valid_until: date | None = None
    notes: str | None = Field(default=None, max_length=500)


class DiscountDashboard(BaseModel):
    """Captured / missed / projected-savings rollup for the discounts dashboard."""

    captured_count: int
    captured_amount: MoneyAmount
    missed_count: int
    missed_amount: MoneyAmount
    capture_rate_pct: PercentNumber
    open_offer_count: int
    projected_savings: MoneyAmount  # net benefit of accepting all worthwhile open offers
    currency: str
