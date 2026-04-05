import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field


class InvoiceStatus(str, Enum):
    new = "new"
    pending = "pending"
    ready_for_review = "ready_for_review"
    approved = "approved"
    rejected = "rejected"
    sending_to_erp = "sending_to_erp"
    sent_to_erp = "sent_to_erp"
    done = "done"
    failed = "failed"


class InvoiceBase(BaseModel):
    vendor: str = Field(..., max_length=255)
    invoice_number: str = Field(..., max_length=100)
    amount: Decimal = Field(..., ge=0)
    currency: str = Field(default="USD", max_length=3)
    invoice_date: date | None = None
    received_date: date | None = None
    due_date: date | None = None
    payment_terms: str | None = Field(default=None, max_length=50)
    status: InvoiceStatus = InvoiceStatus.new
    po_number: str | None = Field(default=None, max_length=100)
    subtotal: Decimal | None = Field(default=None, ge=0)
    tax_amount: Decimal | None = Field(default=None, ge=0)
    discount_amount: Decimal | None = Field(default=None, ge=0)
    shipping_amount: Decimal | None = Field(default=None, ge=0)
    remit_to_address: str | None = None
    bill_to_address: str | None = None
    description: str | None = None
    notes: str | None = None
    gl_account: str | None = Field(default=None, max_length=100)
    cost_center: str | None = Field(default=None, max_length=100)


class InvoiceCreate(InvoiceBase):
    pass


class InvoiceUpdate(BaseModel):
    vendor: str | None = Field(default=None, max_length=255)
    invoice_number: str | None = Field(default=None, max_length=100)
    amount: Decimal | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, max_length=3)
    invoice_date: date | None = None
    received_date: date | None = None
    due_date: date | None = None
    payment_terms: str | None = Field(default=None, max_length=50)
    status: InvoiceStatus | None = None
    po_number: str | None = None
    subtotal: Decimal | None = Field(default=None, ge=0)
    tax_amount: Decimal | None = Field(default=None, ge=0)
    discount_amount: Decimal | None = Field(default=None, ge=0)
    shipping_amount: Decimal | None = Field(default=None, ge=0)
    remit_to_address: str | None = None
    bill_to_address: str | None = None
    description: str | None = None
    notes: str | None = None
    gl_account: str | None = Field(default=None, max_length=100)
    cost_center: str | None = Field(default=None, max_length=100)


class InvoiceResponse(BaseModel):
    """Matches the frontend Invoice TypeScript interface exactly."""

    id: str
    correlation_id: str
    vendor: str
    invoice_number: str
    amount: float
    currency: str
    invoice_date: str | None
    received_date: str | None
    due_date: str | None
    payment_terms: str | None
    status: InvoiceStatus
    po_number: str
    subtotal: float | None
    tax_amount: float | None
    discount_amount: float | None
    shipping_amount: float | None
    remit_to_address: str | None
    bill_to_address: str | None
    description: str
    notes: str | None
    approval_date: str | None
    approved_by: str | None
    gl_account: str | None
    cost_center: str | None
    created_at: str
    file_url: str | None
    warnings: list[dict] | None = None

    model_config = {"from_attributes": True}

    @classmethod
    def from_db(cls, inv) -> "InvoiceResponse":
        return cls(
            id=str(inv.id),
            correlation_id=str(inv.correlation_id),
            vendor=inv.vendor_name,
            invoice_number=inv.invoice_number,
            amount=float(inv.amount),
            currency=inv.currency,
            invoice_date=inv.invoice_date.isoformat() if inv.invoice_date else None,
            received_date=inv.received_date.isoformat() if inv.received_date else None,
            due_date=inv.due_date.isoformat() if inv.due_date else None,
            payment_terms=inv.payment_terms,
            status=inv.status,
            po_number=inv.po_number or "",
            subtotal=float(inv.subtotal) if inv.subtotal is not None else None,
            tax_amount=float(inv.tax_amount) if inv.tax_amount is not None else None,
            discount_amount=float(inv.discount_amount) if inv.discount_amount is not None else None,
            shipping_amount=float(inv.shipping_amount) if inv.shipping_amount is not None else None,
            remit_to_address=inv.remit_to_address,
            bill_to_address=inv.bill_to_address,
            description=inv.description or "",
            notes=inv.notes,
            approval_date=inv.approval_date.isoformat() if inv.approval_date else None,
            approved_by=inv.approved_by,
            gl_account=inv.gl_account,
            cost_center=inv.cost_center,
            created_at=inv.created_at.isoformat() if inv.created_at else "",
            file_url=inv.file_url,
            warnings=inv.warnings,
        )


class InvoiceListResponse(BaseModel):
    items: list[InvoiceResponse]
    total: int
    page: int
    page_size: int


class InvoiceLineItemResponse(BaseModel):
    id: str
    line_number: int | None
    item_code: str | None
    description: str | None
    quantity: float | None
    unit_price: float | None
    tax: float | None
    total: float | None
    gl_account: str | None

    model_config = {"from_attributes": True}
