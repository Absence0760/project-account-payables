"""Base payment adapter interface.

Adapters are responsible for the *external* leg of paying a vendor: handing
the payment off to a bank or processor and returning a tracking ID. The
internal Payment row is owned by `app.api.payments` — adapters don't touch
the DB.

Status lifecycle:

    pending → submitted → completed
                       ↘ failed
                       ↘ cancelled

`pending` means we've created the row locally but haven't called the
processor yet (a transient state during execute_payment_run). `submitted`
means the processor accepted the request — money is in flight but not yet
settled. `completed` is the terminal success state. `failed` and
`cancelled` are terminal too. Webhooks drive the submitted → completed
transition; we don't poll.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from decimal import Decimal


class PaymentStatus(enum.StrEnum):
    pending = "pending"
    submitted = "submitted"
    processing = "processing"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


# Subset that adapters use to report initial submission outcome. Webhooks
# move payments from `submitted` → `completed` / `failed` later.
TERMINAL_STATUSES: frozenset[PaymentStatus] = frozenset(
    {PaymentStatus.completed, PaymentStatus.failed, PaymentStatus.cancelled}
)


@dataclass
class PaymentPayload:
    """Everything an adapter needs to submit one payment.

    `correlation_id` is sent as the processor's idempotency key so a retry
    doesn't double-pay. `vendor_bank` is intentionally a free-form dict —
    each processor models bank accounts differently (Modern Treasury uses
    `counterparty_id`; Increase uses an external account ID; mock ignores it).
    The orchestrator looks up the right shape per processor.
    """

    correlation_id: str
    invoice_id: str
    invoice_number: str
    vendor_name: str
    amount: Decimal
    currency: str
    method: str  # "ach" | "wire" | "check" | "rtp"
    description: str | None = None
    vendor_bank: dict | None = None
    metadata: dict | None = None


@dataclass
class PaymentResult:
    """Outcome of `create_payment`. Reference is whatever the processor
    returns to identify the transaction (Modern Treasury Payment ID, ACH
    trace number, check number, etc.). Status reflects the *immediate*
    response — webhooks drive subsequent transitions."""

    success: bool
    status: PaymentStatus
    provider_payment_id: str | None = None
    reference: str | None = None
    failure_reason: str | None = None
    raw_response: dict | None = None


@dataclass
class WebhookEvent:
    """Normalised representation of a webhook from the processor.

    Adapters parse provider-specific webhook bodies into this shape so the
    common handler in `app.api.payments` can route by `provider_payment_id`
    without knowing the processor's wire format.
    """

    provider_payment_id: str
    status: PaymentStatus
    reference: str | None = None
    failure_reason: str | None = None
    occurred_at: str | None = None  # ISO8601, processor's timestamp
    raw: dict = field(default_factory=dict)


class PaymentAdapter:
    """Base class for payment processor integrations.

    Adapters MUST be stateless — same instance is reused across requests.
    Anything tenant-specific is in `self.config`.
    """

    provider_name: str = "base"
    supported_methods: tuple[str, ...] = ()  # which `method` values this adapter accepts

    def __init__(self, config: dict):
        self.config = config

    async def create_payment(self, payload: PaymentPayload) -> PaymentResult:
        """Submit one payment to the processor. Idempotent by `correlation_id`."""
        raise NotImplementedError

    async def get_payment_status(self, provider_payment_id: str) -> PaymentStatus:
        """Re-fetch status from the processor. Used for reconciliation jobs
        when a webhook is suspected to be missing."""
        raise NotImplementedError

    def parse_webhook(self, headers: dict, body: bytes) -> WebhookEvent | None:
        """Parse + verify a webhook from the processor. Returns None if the
        signature doesn't match or the event isn't a payment status update
        we care about. Raises only on programmer error, never on bad input."""
        raise NotImplementedError

    async def test_connection(self) -> bool:
        """Hit a cheap read endpoint to verify credentials work."""
        raise NotImplementedError
