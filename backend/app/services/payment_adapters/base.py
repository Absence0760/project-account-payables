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

    International fields are populated by
    `services.international_payments.prepare_international_payment` when
    the corridor needs an FX leg or a foreign rail. Adapters that don't
    support international rails ignore them; ones that do (Wise, future
    Tipalti) use them to set the FX quote ID + destination corridor on
    the outbound request.
    """

    correlation_id: str
    invoice_id: str
    invoice_number: str
    vendor_name: str
    amount: Decimal
    currency: str
    method: str  # "ach" | "wire" | "rtp" | "check" | "sepa" | "international_wire"
    description: str | None = None
    vendor_bank: dict | None = None
    metadata: dict | None = None
    # International leg — None on domestic same-currency payments.
    source_currency: str | None = None
    source_amount: Decimal | None = None
    fx_rate: Decimal | None = None
    target_country: str | None = None


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
class CorridorQuote:
    """Per-provider price quote for one payment.

    Returned by `quote_payment` and aggregated by
    `services.corridor_quotes.compare_quotes` to pick the
    cheapest / fastest route across N enabled processors.

    `flat_fee` and `pct_fee` together describe the fee structure:
    total = flat_fee + amount * pct_fee. The aggregator computes the
    realized total for a given amount and ranks. `eta_business_days`
    is the processor's stated settlement SLA; the aggregator uses it
    for the "fastest" tiebreaker.

    `available` is the load-bearing field: an adapter that doesn't
    support the requested (method, currency_pair, country) corridor
    returns `available=False` so the aggregator skips it. The
    `unavailable_reason` carries the why for debugging.
    """

    provider: str
    method: str
    available: bool
    flat_fee: Decimal = Decimal("0")
    pct_fee: Decimal = Decimal("0")
    eta_business_days: int = 0
    fx_rate: Decimal | None = None
    unavailable_reason: str | None = None

    def total_cost(self, amount: Decimal) -> Decimal:
        """Realised cost in source currency for the given amount.

        Unavailable corridors return Decimal("Infinity") so they
        never win a min() comparison. We use the unbounded value
        instead of None so callers don't have to filter twice."""
        if not self.available:
            return Decimal("Infinity")
        return self.flat_fee + (amount * self.pct_fee)


@dataclass
class BalanceResult:
    """Current available balance on the org's funding/operating account at the
    processor, used to auto-seed the cash-position dashboard's opening balance.

    `available` is the load-bearing field (mirrors `CorridorQuote`): an adapter
    that can't report a balance — no bank link, no balance endpoint, transport
    failure — returns ``BalanceResult(available=False, ...)`` and the caller
    falls back to the org's bring-your-own opening balance. `amount` is exact
    (`Decimal`, never float) and only meaningful when `available` is True.
    """

    available: bool
    amount: Decimal = Decimal("0")
    currency: str = "USD"
    account_ref: str | None = None  # opaque account label for the UI — never a full account number
    unavailable_reason: str | None = None


def minor_units_to_decimal(raw: object) -> Decimal | None:
    """Convert a processor's minor-unit amount (cents) to an exact `Decimal`.

    The inverse of the `amount * 100` every minor-unit adapter applies on
    submit, so the round-trip is symmetric by construction — a currency whose
    exponent isn't 2 is mis-scaled identically in both directions and can
    never produce a phantom settlement mismatch.

    Returns None for anything unparseable (a missing key, `null`, a string
    the provider didn't promise): the settlement verifier treats "no reported
    amount" as `unverified`, never as evidence of a discrepancy, so a
    tolerant parse here is the fail-open branch and not a swallowed error.
    """
    if raw is None or isinstance(raw, bool):
        return None
    try:
        return (Decimal(str(raw)) / Decimal("100")).quantize(Decimal("0.01"))
    except (ArithmeticError, ValueError):
        return None


def parse_amount(raw: object) -> Decimal | None:
    """Parse a processor's major-unit amount (a decimal string or number).

    Same tolerant contract as `minor_units_to_decimal` — see its docstring
    for why an unparseable value is None rather than a raise.
    """
    if raw is None or isinstance(raw, bool):
        return None
    try:
        return Decimal(str(raw)).quantize(Decimal("0.01"))
    except (ArithmeticError, ValueError):
        return None


@dataclass
class WebhookEvent:
    """Normalised representation of a webhook from the processor.

    Adapters parse provider-specific webhook bodies into this shape so the
    common handler in `app.api.payments` can route by `provider_payment_id`
    without knowing the processor's wire format.

    ``event_id`` is the processor's own event identifier (NOT the payment
    id) — webhooks retry on any non-2xx, so the handler dedupes by
    ``event_id`` against the Redis ``is_event_already_processed`` ledger
    before mutating state. Adapters that don't expose a stable event id
    should fall back to a composite key (e.g. ``f"{payment_id}:{status}"``)
    that's unique per state transition.

    ``amount`` / ``currency`` are what the processor says it actually
    SETTLED, in major units — the evidence
    `services.payment_settlement.verify_settlement` compares against the
    amount AP authorized before the handler treats a `completed` event as a
    clean settlement. Both stay ``None`` for a provider whose status webhook
    genuinely doesn't carry the figure (Dwolla's transfer events are a bare
    `{topic, resourceId}`); that reads as `unverified`, never as a
    discrepancy. Adapters converting from minor units should use
    ``minor_units_to_decimal`` so the round-trip stays symmetric with what
    they sent on submit.
    """

    provider_payment_id: str
    status: PaymentStatus
    event_id: str = ""
    reference: str | None = None
    failure_reason: str | None = None
    occurred_at: str | None = None  # ISO8601, processor's timestamp
    amount: Decimal | None = None  # settled amount, MAJOR units
    currency: str | None = None  # ISO-4217, as reported
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

    async def void_payment(self, provider_payment_id: str) -> bool:
        """Reverse / cancel an in-flight or settled payment upstream.

        Optional — adapters that don't support it should return False.
        Returning True means the processor accepted the void; the caller
        is responsible for the local bookkeeping (flipping the row,
        re-opening the invoice, audit log).

        Raises only on programmer error or transport failure; the caller
        treats exceptions as "void rejected" and records an adapter_error
        outcome on the audit row.
        """
        return False

    async def get_balance(self) -> BalanceResult:
        """Report the current available balance on the org's funding account.

        OPTIONAL capability — the default returns
        ``BalanceResult(available=False, unavailable_reason="not_supported")``
        so adapters that have no balance endpoint (or aren't bank-linked) are
        unaffected and the caller transparently falls back to the manual
        bring-your-own opening balance. Concrete adapters override this with a
        live read against the processor (mock returns a deterministic figure so
        local dev needs no real bank credential).

        Best-effort by contract: implementations should catch transport
        failures and return ``available=False`` rather than raise. The
        cash-position endpoint also guards the call, so a balance fetch never
        500s the dashboard.
        """
        return BalanceResult(available=False, unavailable_reason="not_supported")

    async def quote_payment(self, payload: PaymentPayload) -> CorridorQuote:
        """Return a price quote for this payment WITHOUT submitting it.

        Used by `services.corridor_quotes.compare_quotes` to pick the
        cheapest of N configured processors. Default implementation
        reports `available=True` iff the payload's `method` is in
        `supported_methods`, with zero fees — concrete adapters
        override this with their real fee schedule + a live call to
        the processor's quote endpoint (Wise, Tipalti) when available,
        or a static fee table when not (Modern Treasury).

        Adapters that genuinely can't quote (no static table, no
        live endpoint) MUST return `CorridorQuote(available=False,
        unavailable_reason="no_quote_endpoint")` so the aggregator
        falls back to the next provider.
        """
        if payload.method not in self.supported_methods:
            return CorridorQuote(
                provider=self.provider_name,
                method=payload.method,
                available=False,
                unavailable_reason=(
                    f"method '{payload.method}' not supported by {self.provider_name}"
                ),
            )
        return CorridorQuote(
            provider=self.provider_name,
            method=payload.method,
            available=True,
            flat_fee=Decimal("0"),
            pct_fee=Decimal("0"),
            eta_business_days=0,
        )
