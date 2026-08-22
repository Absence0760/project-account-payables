"""Schemas for virtual card endpoints."""

from decimal import Decimal

from pydantic import BaseModel, Field

from app.api.pagination import PageMeta
from app.schemas.money import MoneyAmount, OptionalMoneyAmount


class GenerateCardsRequest(BaseModel):
    invoice_ids: list[str] = Field(..., min_length=1)


class CardResponse(BaseModel):
    id: str
    invoice_id: str
    vendor_id: str | None
    card_provider: str
    last_four: str | None
    # Money is exact: Decimal in Python (never float), serialised to a JSON
    # number for the SPA. The DB columns are Numeric(15, 2).
    amount_limit: MoneyAmount
    amount_charged: OptionalMoneyAmount = None
    currency: str
    status: str
    expires_at: str | None
    sent_at: str | None
    charged_at: str | None
    merchant_name: str | None
    decline_reason: str | None
    created_at: str

    # Joined fields
    vendor_name: str | None = None
    invoice_number: str | None = None

    model_config = {"from_attributes": True}


class CardListResponse(PageMeta):
    items: list[CardResponse]
    total: int


class CardDetailsResponse(BaseModel):
    card_number: str
    exp_month: int
    exp_year: int
    cvv: str


class RebateStatusBreakdown(BaseModel):
    """A rebate total split by `CardRebate.status` lifecycle bucket.

    `pending_total` is the processor's ESTIMATE — not yet confirmed by the
    processor's out-of-band settlement, and not yet money in the bank.
    `confirmed_total` is processor-confirmed but not yet disbursed;
    `paid_out_total` has actually landed. Only `confirmed_total +
    paid_out_total` is realized money — see `CardDashboardResponse`.
    """

    pending_total: MoneyAmount
    confirmed_total: MoneyAmount
    paid_out_total: MoneyAmount


class CardDashboardResponse(BaseModel):
    active_cards: int
    active_cards_value: MoneyAmount
    spend_this_month: MoneyAmount
    # REALIZED rebates only — `confirmed` + `paid_out` `CardRebate` rows.
    # A `pending` rebate is the processor's estimate, not yet confirmed by
    # its own out-of-band settlement (see `POST /rebates/{id}/confirm`) and
    # nowhere close to a bank deposit; blending it into "Rebates Earned"
    # used to let 100% of a headline "earned" figure be entirely
    # unconfirmed. See `rebates_this_month_by_status` / `rebates_ytd_by_status`
    # for the full pending/confirmed/paid_out breakdown.
    rebates_this_month: MoneyAmount
    rebates_ytd: MoneyAmount
    # Projected annual = (rebates_ytd / months_elapsed) × 12, off the same
    # REALIZED (confirmed + paid_out) YTD figure above — never `pending`.
    # Falls back to rebates_this_month × 12 in January when YTD is short.
    # Surfaces to the rebate dashboard as the "on track for" headline.
    projected_annual_rebates: MoneyAmount = Decimal("0")
    rebates_this_month_by_status: RebateStatusBreakdown
    rebates_ytd_by_status: RebateStatusBreakdown


class RebateResponse(BaseModel):
    id: str
    virtual_card_id: str
    amount: MoneyAmount
    # The negotiated rebate ratio (e.g. 0.0125 = 1.25%) — a Decimal in the DB;
    # kept exact in Python, serialised to a JSON number like the money fields.
    rate: MoneyAmount
    status: str
    period: str | None
    created_at: str


class RebateListResponse(BaseModel):
    items: list[RebateResponse]
    total: MoneyAmount  # total rebate amount
