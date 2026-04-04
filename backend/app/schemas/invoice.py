import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field


class InvoiceStatus(str, Enum):
    new = "new"
    pending = "pending"
    ready_for_review = "ready_for_review"
    failed = "failed"
    sent_to_erp = "sent_to_erp"


class InvoiceBase(BaseModel):
    vendor: str = Field(..., max_length=255)
    invoice_number: str = Field(..., max_length=100)
    amount: Decimal = Field(..., ge=0)
    currency: str = Field(default="USD", max_length=3)
    due_date: date | None = None
    status: InvoiceStatus = InvoiceStatus.new
    po_number: str | None = Field(default=None, max_length=100)
    description: str | None = None


class InvoiceCreate(InvoiceBase):
    pass


class InvoiceUpdate(BaseModel):
    vendor: str | None = Field(default=None, max_length=255)
    invoice_number: str | None = Field(default=None, max_length=100)
    amount: Decimal | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, max_length=3)
    due_date: date | None = None
    status: InvoiceStatus | None = None
    po_number: str | None = None
    description: str | None = None


class InvoiceResponse(BaseModel):
    """Matches the frontend Invoice TypeScript interface exactly."""

    id: str
    vendor: str
    invoice_number: str
    amount: float
    currency: str
    due_date: str | None
    status: InvoiceStatus
    po_number: str
    description: str
    created_at: str
    file_url: str | None

    model_config = {"from_attributes": True}

    @classmethod
    def from_db(cls, inv) -> "InvoiceResponse":
        return cls(
            id=str(inv.id),
            vendor=inv.vendor_name,
            invoice_number=inv.invoice_number,
            amount=float(inv.amount),
            currency=inv.currency,
            due_date=inv.due_date.isoformat() if inv.due_date else None,
            status=inv.status,
            po_number=inv.po_number or "",
            description=inv.description or "",
            created_at=inv.created_at.isoformat() if inv.created_at else "",
            file_url=inv.file_url,
        )


class InvoiceListResponse(BaseModel):
    items: list[InvoiceResponse]
    total: int
    page: int
    page_size: int


class InvoiceLineItemResponse(BaseModel):
    id: str
    description: str | None
    quantity: float | None
    unit_price: float | None
    tax: float | None
    total: float | None

    model_config = {"from_attributes": True}
