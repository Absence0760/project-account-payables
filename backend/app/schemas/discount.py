"""Pydantic schemas for dynamic discounting & early-payment optimization.

Shared contract for the ``/api/discounts`` router and the frontend. Money
fields use the ``MoneyAmount`` annotation (Decimal in Python, JSON number on
the wire); tier/ROI percents stay ``Decimal`` for exactness. See
``backend/docs/dynamic-discounting.md``.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, PlainSerializer

from app.schemas.money import MoneyAmount, OptionalMoneyAmount


def _decimal_to_number(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


# A non-money Decimal (a percentage / rate) that serialises to a JSON *number*,
# matching the frontend's `number`-typed contract while staying exact in Python.
PercentNumber = Annotated[
    Decimal,
    PlainSerializer(_decimal_to_number, return_type=float, when_used="json"),
]


def _parse_exact_money(value: object) -> object:
    """Parse an INBOUND money value without ever routing it through ``float``.

    ``json.loads`` decodes the request body before any pydantic validator runs,
    so a JSON *number* carrying a fractional part is already a Python ``float``
    by the time this sees it — the rounding has happened and nothing downstream
    can undo it. Typing the field ``Decimal`` does not help: pydantic returns
    ``Decimal('100')`` for a body containing ``100.00000000000000001``, because
    that literal was a float long before pydantic was involved.

    Only the **string** form round-trips exactly, so that is the shape a
    fractional amount must arrive in. A JSON integer is admitted as well —
    ``json.loads`` yields a Python ``int`` for it, which is exact, and it is the
    shape existing callers already send. A ``float`` is refused with a message
    naming the fix, rather than silently accepted at whatever value the double
    happened to round to: this is a spend decision (`optimize` chooses which
    invoices get paid early), not a display value. Root ``CLAUDE.md``
    § Project invariants — money is ``Decimal``, never ``float``.

    Lives here because ``/api/discounts`` is the only request surface that
    needs it today; promote it to ``app/schemas/money.py`` beside the response
    annotations the moment a second router wants the same rule.
    """
    if value is None or isinstance(value, Decimal):
        return value
    if isinstance(value, bool):  # `bool` is an `int` — never a money amount.
        raise ValueError("must be a decimal string, not a boolean")
    if isinstance(value, float):
        raise ValueError(
            'send this amount as a decimal STRING (e.g. "1234.56"); a JSON number '
            "is parsed as a float and loses exactness before it reaches the server"
        )
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("must be a decimal string, not an empty string")
        try:
            parsed = Decimal(text)
        except InvalidOperation:
            raise ValueError(f"{value!r} is not a valid decimal amount") from None
        if not parsed.is_finite():
            raise ValueError("must be a finite decimal amount")
        return parsed
    raise ValueError("must be a decimal string")


# An inbound money amount that is exact by construction. See
# `_parse_exact_money` for why a bare `Decimal` annotation is not enough.
ExactMoneyInput = Annotated[Decimal, BeforeValidator(_parse_exact_money)]


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
    # Digits match `discount_offers.base_amount` Numeric(15, 2).
    base_amount: Decimal | None = Field(default=None, ge=0, max_digits=15, decimal_places=2)
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
    # The currency THIS row's money is in — `roi.savings` is computed from the
    # offer's own `base_amount`, so it is the OFFER's currency, not the
    # response-level `currency` the totals are summed in. Stated per row
    # because the two differ exactly when `unconvertible` is set, and a client
    # that cannot name a figure's currency has to render it bare.
    currency: str = "USD"
    selected: bool  # True if it fits within the cash budget
    cumulative_outlay: MoneyAmount  # running cash committed through this rank
    # This offer's money is in a currency the totals are NOT in, so it is
    # excluded from every total (and, when a cash budget binds, from selection).
    # Its ROI percentages remain meaningful — a rate is currency-free.
    unconvertible: bool = False


class OptimizerRequest(BaseModel):
    """Request body for ``POST /api/discounts/optimize``.

    This endpoint took a bare ``dict`` and did ``Decimal(str(body["cash_budget"]))``,
    which is exact only by accident: by then ``json.loads`` had already turned a
    JSON number into a ``float``, so the budget the optimizer selected against
    was the rounded double, not what the caller sent. A bare dict on a money
    path also meant a malformed value reached ``Decimal()`` and surfaced as a
    500 rather than a 422.

    ``extra="forbid"`` on purpose: with a free-form dict, a misspelled key
    (``cashBudget``) silently ran the optimizer *unconstrained* and returned a
    plan committing more cash than the caller asked for. A 422 is the honest
    answer to a budget we did not understand.
    """

    model_config = ConfigDict(extra="forbid")

    # Optional — `None` means "no budget", which selects every worthwhile
    # opportunity. Accepted as an exact decimal string; see `_parse_exact_money`.
    cash_budget: Annotated[Decimal | None, BeforeValidator(_parse_exact_money)] = Field(
        default=None, ge=0
    )


class OptimizerResponse(BaseModel):
    cash_budget: OptionalMoneyAmount = None
    # The currency EVERY money total below is denominated in (the org's
    # reporting currency) — stated rather than assumed, because the totals are
    # sums across offers and offers carry their own currencies.
    currency: str = "USD"
    cost_of_capital_pct: PercentNumber
    total_savings_available: MoneyAmount
    total_savings_selected: MoneyAmount
    total_outlay_selected: MoneyAmount
    # Ranked offers left out of the totals because they are in another currency.
    unconvertible_count: int = 0
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
    # Open offers excluded from `projected_savings` because they are denominated
    # in a currency other than `currency` — never summed in at face value.
    unconvertible_offer_count: int = 0
    # The same honesty for the two realised figures: `captured_amount` and
    # `missed_amount` count only offers denominated in `currency`, and these say
    # how many were left out. A bare cross-currency SUM presented under one code
    # is not a smaller number than the truth, it is a different quantity.
    excluded_captured_count: int = 0
    excluded_missed_count: int = 0
