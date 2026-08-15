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
    """Every status `PaymentRun.status` can actually hold.

    `executing`, `partial` and `cancelled` were missing even though the code
    has always written them: `/runs/{id}/execute` claims a run by flipping it
    to `executing`, `_dispatch_run_payments` rolls up to `partial` when some
    payments succeeded and some failed, and `/runs/{id}/cancel` writes
    `cancelled`. An enum that can't name three of the eight real values is a
    filter/validation surface that silently disagrees with the table.
    """

    draft = "draft"
    executing = "executing"
    submitted = "submitted"
    processing = "processing"
    partial = "partial"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


PAYMENT_STATUSES = [s.value for s in PaymentStatus]
PAYMENT_RUN_STATUSES = [s.value for s in PaymentRunStatus]


class PaymentCreate(BaseModel):
    invoice_id: str
    # Optional, and only ever a cross-check: the server binds the payment to
    # the invoice amount net of applied credit memos and 422s a value that
    # disagrees (see `api/payments.create_payment`). Required-ness here was a
    # trap once netting landed — a caller that knows the invoice amount but not
    # its credits had no figure it could legally send.
    amount: Decimal | None = Field(default=None, ge=0)
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

    # Why this payment failed, and when it moved. `Payment.failure_reason` has
    # existed (and been populated on every failure path — compliance refusal,
    # card-issuance failure, adapter error, void, webhook failure) since the
    # model was written, but it never reached the read surface: a run could
    # report "2 failed" and the UI had no way to say why, so the operator's
    # only recourse was the server log. Readable by the payments roles
    # (admin / ap_manager / cfo), who can already read vendor bank details —
    # this is not a new exposure class.
    provider: str | None = None
    failure_reason: str | None = None
    submitted_at: str | None = None
    completed_at: str | None = None

    # What the PROCESSOR says it moved, beside `amount` which is what AP
    # AUTHORIZED (migration 0083). Without these on the read surface an
    # operator seeing a `completed` payment whose invoice is stuck at
    # `payment_scheduled` has no way to tell why — the shortfall would only be
    # visible by cross-referencing the audit log or a `fraud_flag` exception
    # that doesn't name the figures. Same reasoning as `failure_reason` above,
    # and the same audience.
    #
    # `None` here means no rail ever reported a figure, NOT zero — see
    # `payment_settlement.settlement_coverage`.
    settled_amount: OptionalMoneyAmount = None
    settled_currency: str | None = None

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
            provider=p.provider,
            failure_reason=p.failure_reason,
            submitted_at=p.submitted_at.isoformat() if p.submitted_at else None,
            completed_at=p.completed_at.isoformat() if p.completed_at else None,
            settled_amount=getattr(p, "settled_amount", None),
            settled_currency=getattr(p, "settled_currency", None),
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

    # Per-outcome tallies over the run's own payments. `partial` alone doesn't
    # tell an operator whether one payment failed or forty, and the counts used
    # to exist only in the transient response body of the /execute call that
    # produced them — reload the page and they were gone. Derived on read from
    # the child `Payment` rows (no stored running total, matching the budget
    # service's compute-on-read posture), so they can never drift from the
    # payments they summarise.
    payments_completed: int = 0
    payments_failed: int = 0
    payments_in_flight: int = 0
    payments_pending: int = 0

    model_config = {"from_attributes": True}

    @classmethod
    def from_db(
        cls,
        pr,
        payment_count: int = 0,
        *,
        completed: int = 0,
        failed: int = 0,
        in_flight: int = 0,
        pending: int = 0,
    ) -> "PaymentRunResponse":
        return cls(
            id=str(pr.id),
            status=pr.status,
            total_amount=pr.total_amount,
            initiated_by=str(pr.initiated_by) if pr.initiated_by else None,
            executed_at=pr.executed_at.isoformat() if pr.executed_at else None,
            created_at=pr.created_at.isoformat() if pr.created_at else "",
            payment_count=payment_count,
            payments_completed=completed,
            payments_failed=failed,
            payments_in_flight=in_flight,
            payments_pending=pending,
        )


class PaymentRunListResponse(BaseModel):
    items: list[PaymentRunResponse]
    total: int
    page: int
    page_size: int
