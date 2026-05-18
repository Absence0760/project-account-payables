from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field

from app.schemas.money import MoneyAmount, OptionalMoneyAmount


class PaymentStatus(StrEnum):
    pending = "pending"
    processing = "processing"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class PaymentMethod(StrEnum):
    ach = "ach"
    wire = "wire"
    check = "check"
    virtual_card = "virtual_card"


class PaymentRunStatus(StrEnum):
    draft = "draft"
    submitted = "submitted"
    processing = "processing"
    completed = "completed"
    failed = "failed"


PAYMENT_STATUSES = [s.value for s in PaymentStatus]
PAYMENT_RUN_STATUSES = [s.value for s in PaymentRunStatus]


class PaymentCreate(BaseModel):
    invoice_id: str
    amount: Decimal = Field(..., ge=0)
    method: PaymentMethod | None = None
    reference: str | None = Field(default=None, max_length=255)
    payment_run_id: str | None = None


class PaymentResponse(BaseModel):
    id: str
    correlation_id: str | None
    invoice_id: str
    payment_run_id: str | None
    amount: MoneyAmount
    method: str | None
    status: str
    reference: str | None
    created_at: str
    updated_at: str | None

    # Joined fields from invoice
    vendor_name: str | None = None
    invoice_number: str | None = None

    # Card metadata: surfaced when method=virtual_card so the History row
    # can show "•••• 1234 · lithic" without an extra request.
    card_last_four: str | None = None
    card_provider: str | None = None
    card_id: str | None = None

    model_config = {"from_attributes": True}

    @classmethod
    def from_db(cls, p, invoice=None, card=None) -> "PaymentResponse":
        return cls(
            id=str(p.id),
            correlation_id=str(p.correlation_id) if p.correlation_id else None,
            invoice_id=str(p.invoice_id),
            payment_run_id=str(p.payment_run_id) if p.payment_run_id else None,
            amount=p.amount,
            method=p.method,
            status=p.status,
            reference=p.reference,
            created_at=p.created_at.isoformat() if p.created_at else "",
            updated_at=p.updated_at.isoformat() if p.updated_at else None,
            vendor_name=invoice.vendor_name if invoice else None,
            invoice_number=invoice.invoice_number if invoice else None,
            card_last_four=card.last_four if card else None,
            card_provider=card.card_provider if card else None,
            card_id=str(card.id) if card else None,
        )


class PaymentListResponse(BaseModel):
    items: list[PaymentResponse]
    total: int
    page: int
    page_size: int


class PaymentRunResponse(BaseModel):
    id: str
    status: str
    total_amount: OptionalMoneyAmount = None
    initiated_by: str | None
    executed_at: str | None
    created_at: str
    payment_count: int = 0

    model_config = {"from_attributes": True}

    @classmethod
    def from_db(cls, pr, payment_count: int = 0) -> "PaymentRunResponse":
        return cls(
            id=str(pr.id),
            status=pr.status,
            total_amount=pr.total_amount,
            initiated_by=str(pr.initiated_by) if pr.initiated_by else None,
            executed_at=pr.executed_at.isoformat() if pr.executed_at else None,
            created_at=pr.created_at.isoformat() if pr.created_at else "",
            payment_count=payment_count,
        )


class PaymentRunListResponse(BaseModel):
    items: list[PaymentRunResponse]
    total: int
    page: int
    page_size: int
