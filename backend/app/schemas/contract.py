"""Pydantic request/response schemas for the contracts router."""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field

from app.api.pagination import PageMeta
from app.models.contract import ContractType


class ContractLineItemBase(BaseModel):
    line_number: int | None = None
    item_code: str | None = Field(default=None, max_length=100)
    description: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    total: Decimal | None = None
    gl_account: str | None = Field(default=None, max_length=100)


class ContractLineItemResponse(ContractLineItemBase):
    id: str


class ContractBase(BaseModel):
    contract_number: str = Field(..., max_length=100)
    title: str | None = Field(default=None, max_length=255)
    description: str | None = None
    contract_type: ContractType = ContractType.purchase
    currency: str = Field(default="USD", max_length=3)
    total_value: Decimal | None = None
    spend_limit: Decimal | None = None
    not_to_exceed: bool = False
    start_date: date | None = None
    end_date: date | None = None
    signed_date: date | None = None
    auto_renew: bool = False
    renewal_term_months: int | None = Field(default=None, ge=0)
    renewal_notice_days: int = Field(default=30, ge=0, le=3650)
    payment_terms: str | None = Field(default=None, max_length=100)
    owner_user_id: str | None = None
    terms: dict | None = None


class ContractCreate(ContractBase):
    vendor_id: str
    line_items: list[ContractLineItemBase] = Field(default_factory=list)


class ContractUpdate(BaseModel):
    """PATCH — every field optional. Status changes go through the dedicated
    lifecycle endpoints (activate / terminate / cancel / renew), not here."""

    contract_number: str | None = Field(default=None, max_length=100)
    title: str | None = Field(default=None, max_length=255)
    description: str | None = None
    contract_type: ContractType | None = None
    vendor_id: str | None = None
    currency: str | None = Field(default=None, max_length=3)
    total_value: Decimal | None = None
    spend_limit: Decimal | None = None
    not_to_exceed: bool | None = None
    start_date: date | None = None
    end_date: date | None = None
    signed_date: date | None = None
    auto_renew: bool | None = None
    renewal_term_months: int | None = Field(default=None, ge=0)
    renewal_notice_days: int | None = Field(default=None, ge=0, le=3650)
    payment_terms: str | None = Field(default=None, max_length=100)
    owner_user_id: str | None = None
    terms: dict | None = None


class ContractRenew(BaseModel):
    """Extend a contract to a new end date (and optionally bump value)."""

    end_date: date
    total_value: Decimal | None = None
    spend_limit: Decimal | None = None


class ContractSpendSummary(BaseModel):
    """Spend rolled up against the contract from its linked invoices."""

    invoiced_total: float
    invoice_count: int
    spend_limit: float | None
    remaining: float | None
    over_limit: bool


class ContractResponse(BaseModel):
    id: str
    contract_number: str
    title: str | None
    description: str | None
    contract_type: str
    status: str
    vendor_id: str
    vendor_name: str | None
    currency: str
    total_value: float | None
    spend_limit: float | None
    not_to_exceed: bool
    start_date: str | None
    end_date: str | None
    signed_date: str | None
    auto_renew: bool
    renewal_term_months: int | None
    renewal_notice_days: int
    renewal_alert_sent_at: str | None
    payment_terms: str | None
    owner_user_id: str | None
    file_url: str | None
    file_key: str | None
    terms: dict | None
    line_items: list[ContractLineItemResponse] = Field(default_factory=list)
    spend: ContractSpendSummary | None = None
    created_at: str
    updated_at: str


class ContractListResponse(PageMeta):
    items: list[ContractResponse]
    total: int
