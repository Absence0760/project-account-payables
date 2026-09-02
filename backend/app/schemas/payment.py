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
    # UK domestic rails. A GBP→GB domestic payment used to fall through to
    # `international_wire` (SWIFT + a 2.5% fee anchor) because the corridor
    # selector had no domestic-GB branch and none of these rails existed;
    # `payment_corridor.pick_corridor` now routes a same-currency GBP/GB
    # payment onto Faster Payments, with BACS / CHAPS available as explicit
    # `requested_method` overrides (issue #328). All three are domestic bank
    # rails — 1099-reportable, not card, no FX leg.
    bacs = "bacs"
    faster_payments = "faster_payments"
    chaps = "chaps"


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
    # Digits match `payments.amount` Numeric(15, 2).
    amount: Decimal | None = Field(default=None, ge=0, max_digits=15, decimal_places=2)
    method: PaymentMethod | None = None
    reference: str | None = Field(default=None, max_length=255)
    # No `payment_run_id`. It used to be accepted here and written straight to
    # the FK with no validation at all — the run was never checked to exist, to
    # be `draft`, or to belong to the caller's entity, and `run.total_amount` /
    # `requires_cfo_approval` were not recomputed. That let a caller inject
    # payments into any run: N legs each individually under
    # `payments.cfo_approval_above` inflate a run whose CFO flag was frozen at
    # creation, and `/execute` then dispatches the lot with no sign-off. It also
    # let a payment be attached to an already-terminal run, where nothing ever
    # dispatches it — leaving the row `pending` forever, occupying the invoice's
    # `uq_payments_one_live_per_invoice` slot with `/void` the only exit.
    #
    # A payment that belongs to a run is created BY the run
    # (`services/payment_runs.create_payment_run_for_invoices`), which stamps
    # the FK itself. This endpoint books a STANDALONE payment and gates it with
    # its own per-payment CFO check; there is no legitimate caller. No
    # first-party client ever sent the field, and Pydantic ignores an unknown
    # key, so a stray one is simply not honoured rather than 422-ing.


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
            settled_amount=p.settled_amount,
            settled_currency=p.settled_currency,
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

    # The CFO sign-off gate, on the LIST shape. These columns have always
    # existed on the row and `GET /runs/{id}` (a raw dict) has always returned
    # them — but the list endpoint declares this model, and FastAPI strips
    # whatever a response model doesn't declare. A client that reads the list
    # therefore saw `requires_cfo_approval` as absent for every run, always,
    # and could not tell an above-threshold run from any other: the mobile app
    # renders no "CFO approval required" marker, its pre-flight gate evaluates
    # false, and Execute goes out to a 403. The web app is unaffected only
    # because its run modal happens to read the detail endpoint instead.
    requires_cfo_approval: bool = False
    cfo_approved_at: str | None = None

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
            requires_cfo_approval=bool(getattr(pr, "requires_cfo_approval", False)),
            cfo_approved_at=(
                pr.cfo_approved_at.isoformat() if getattr(pr, "cfo_approved_at", None) else None
            ),
        )


class PaymentRunListResponse(BaseModel):
    items: list[PaymentRunResponse]
    total: int
    page: int
    page_size: int
