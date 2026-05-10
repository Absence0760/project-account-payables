from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field


class CreditMemoCreate(BaseModel):
    memo_number: str = Field(..., max_length=100)
    vendor_id: str
    amount: Decimal = Field(..., gt=0)
    currency: str = Field(default="USD", max_length=3)
    issued_date: date | None = None
    reason: str | None = None
    invoice_id: str | None = None  # optional: link at creation time


class CreditMemoApply(BaseModel):
    invoice_id: str


class CreditMemoResponse(BaseModel):
    id: str
    memo_number: str
    vendor_id: str
    vendor_name: str | None = None
    invoice_id: str | None = None
    invoice_number: str | None = None
    amount: float
    currency: str
    issued_date: str | None = None
    reason: str | None = None
    status: str
    applied_at: str | None = None
    applied_by: str | None = None
    created_at: str


class CreditMemoListResponse(BaseModel):
    items: list[CreditMemoResponse]
    total: int
