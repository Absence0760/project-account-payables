"""Shared payment-run creation logic.

Extracted from ``POST /api/payments/runs`` (`app/api/payments.py`) so a
second caller — the AI Cash-Flow Copilot's draft-run enact route
(``POST /api/cash-flow/plans/{plan_id}/draft-run``) — can stage a draft run
through the EXACT same gates instead of forking them: the payable-status
check, the ``PAYMENT_BLOCKING_EXCEPTION_TYPES`` financial-integrity block,
credit-memo netting, the CFO-approval-threshold computation, and the
``uq_payments_one_live_per_invoice`` idempotency backstop.

This module never commits — the caller owns the session lifecycle (matching
every other router in this codebase) and decides the HTTP response shape.
It also never executes a run; every path here lands ``status="draft"``.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.credit_memo import CreditMemo
from app.models.exception import Exception as InvoiceException
from app.models.invoice import Invoice
from app.models.organization import Organization
from app.models.payment import Payment, PaymentRun
from app.models.user import User
from app.services.currency_conversion import (
    reporting_amount_at_locked_rate,
    resolve_reporting_currency,
)
from app.services.payment_controls import (
    CFO_REASON_AMOUNT_NOT_EXPRESSIBLE,
    CFO_REASON_THRESHOLD_UNPARSEABLE,
    cfo_approval_decision,
)
from app.tenant import apply_entity_scope

logger = logging.getLogger(__name__)


# What each `Payment.status` means to its run's rollup. Declared once because
# three call sites bucket the same statuses: the execute/resume/retry
# dispatcher (`api/payments.py::_dispatch_run_payments`, which is what
# *persists* `run.status`), the run-detail read, and the runs list. A status
# added to one bucket but not the others is exactly how a run comes to report
# an outcome its own payments don't support.
#
# `pending` is deliberately its own bucket and NOT part of the run-status
# derivation: a `pending` payment is one nothing has attempted yet, which is
# the normal state of a `draft` run and the resumable state of a crashed one.
RUN_PAYMENT_COMPLETED_STATUSES = ("completed",)
RUN_PAYMENT_FAILED_STATUSES = ("failed", "cancelled")
RUN_PAYMENT_IN_FLIGHT_STATUSES = ("submitted", "processing", "pending_compliance")
RUN_PAYMENT_PENDING_STATUSES = ("pending",)


@dataclass(frozen=True)
class PaymentRunRollup:
    """Per-outcome tallies over one run's payments, plus the run status they
    add up to. Pure — no DB, no clock."""

    total: int = 0
    completed: int = 0
    failed: int = 0
    in_flight: int = 0
    pending: int = 0

    @property
    def run_status(self) -> str:
        """The `PaymentRun.status` these payment outcomes imply.

        Precedence: all-fail → `failed`, any-fail-with-a-survivor → `partial`,
        anything still in flight → `submitted`, otherwise `completed`.

        The two rungs BELOW `completed` exist because the default used to be
        `completed` — a fail-OPEN answer on a money-run status. A run with
        every payment still `pending` (nothing attempted) and a run with no
        payments at all both reported "completed" without a cent having moved.
        A run whose payments are all `pending` is precisely the resumable
        state, so it reports `executing`; a run with no payments has never been
        dispatched at all, so it reports `draft`. Neither claims success.
        """
        if self.failed and not (self.completed or self.in_flight):
            return "failed"
        if self.failed:
            return "partial"
        if self.in_flight:
            return "submitted"
        if self.total == 0:
            return "draft"
        if self.pending:
            return "executing"
        return "completed"


#: Run statuses that describe a CLAIM on the run, not an outcome of its
#: payments. `draft` = never dispatched, `executing` = a dispatch pass holds it,
#: `cancelled` = abandoned (its payments were deleted). `derive_run_status`
#: passes these straight through: re-deriving them from payment rows would let
#: a rollup un-claim a run mid-dispatch, and `/execute` / `/resume` gate on
#: exactly these values.
CLAIM_RUN_STATUSES: tuple[str, ...] = ("draft", "executing", "cancelled")


def derive_run_status(persisted_status: str | None, rollup: PaymentRunRollup) -> str:
    """The run status to REPORT, given what's persisted and what its payments say.

    ``PaymentRun.status`` is only ever written by the dispatcher's final rollup
    (`api/payments._dispatch_run_payments`) and the claim/cancel transitions.
    Nothing else re-derives it — not the webhook, not the reconciler, not
    `/compliance/{release,dismiss}` — so a run that rolled up `submitted`
    (one payment held `pending_compliance`) and then had that payment dismissed
    kept reporting `submitted` while its own payments said `failed`.
    `/retry-failed` gates on `RETRYABLE_RUN_STATUSES = ("partial", "failed")`,
    and `/execute` / `/resume` gate on the claim states, so that run was a dead
    end: it showed a retryable failure and every button 409ed.

    Deriving on read closes it without a migration and without any writer being
    able to drift from any reader. A claim state is returned untouched (see
    ``CLAIM_RUN_STATUSES``); an outcome state is recomputed from the run's
    ACTIVE payments.
    """
    status = persisted_status or "draft"
    if status in CLAIM_RUN_STATUSES:
        return status
    return rollup.run_status


async def recompute_run_status(db: AsyncSession, run: PaymentRun) -> str:
    """Re-derive and PERSIST ``run.status`` from its payments. Returns the new value.

    Called wherever a payment's status changes outside the dispatch pass — the
    compliance release/dismiss endpoints, `/void`, the processor webhook — so
    the stored value stops lying between reads. Reads go through
    ``derive_run_status`` regardless, so this is belt-and-braces rather than
    the correctness mechanism; it is what keeps a `SELECT status FROM
    payment_runs` (an operator, an export, a future consumer) honest.

    Never commits — the caller's transaction owns that.
    """
    rows = (
        (await db.execute(select(Payment).where(Payment.payment_run_id == run.id))).scalars().all()
    )
    rollup = rollup_payment_statuses(p.status for p in active_run_payments(rows))
    run.status = derive_run_status(run.status, rollup)
    return run.status


def superseded_payment_ids(payments: Iterable[Payment]) -> set[uuid.UUID]:
    """Ids of run payments that a LATER attempt on the same run replaced.

    ``/retry-failed`` never re-arms a failed payment in place — it books a NEW
    ``Payment`` row carrying ``retry_of_payment_id``, leaving attempt #1 as the
    immutable record of a failure that really happened (see
    ``api/payments.retry_failed_payments``). So a run legitimately holds several
    rows for one invoice, and only the newest one describes where that invoice
    actually stands. Every rollup filters through here first; without it a fully
    recovered run would report `partial` forever and keep offering a retry that
    could only ever be skipped.
    """
    return {p.retry_of_payment_id for p in payments if p.retry_of_payment_id is not None}


def active_run_payments(payments: Iterable[Payment]) -> list[Payment]:
    """A run's payments with every superseded earlier attempt filtered out."""
    rows = list(payments)
    superseded = superseded_payment_ids(rows)
    return [p for p in rows if p.id not in superseded]


def rollup_payment_statuses(statuses: Iterable[str | None]) -> PaymentRunRollup:
    """Bucket a run's payment statuses into a `PaymentRunRollup`.

    Callers hand this the ACTIVE payments only (`active_run_payments`) — a
    superseded retry attempt is history, not an outcome.
    """
    total = completed = failed = in_flight = pending = 0
    for status in statuses:
        total += 1
        if status in RUN_PAYMENT_COMPLETED_STATUSES:
            completed += 1
        elif status in RUN_PAYMENT_FAILED_STATUSES:
            failed += 1
        elif status in RUN_PAYMENT_IN_FLIGHT_STATUSES:
            in_flight += 1
        elif status in RUN_PAYMENT_PENDING_STATUSES:
            pending += 1
    return PaymentRunRollup(
        total=total,
        completed=completed,
        failed=failed,
        in_flight=in_flight,
        pending=pending,
    )


# ── Is a failed payment safe to re-attempt? ──────────────────────────────
#
# `Payment.correlation_id` is the PROCESSOR's idempotency key, not a local trace
# id: `payment_adapters/base.py` says so, and it is sent as `Idempotency-Key` by
# column / dwolla / stripe_treasury / increase, as `idempotency_key=` by
# modern_treasury, and as a 48h Redis `SET NX` slot by checkeeper (explicitly so
# a retry can't print a second physical cheque). A retry books a NEW payment row
# with a NEW correlation id — a genuinely new order — which is exactly right
# when the first order never existed, and exactly how an invoice gets paid twice
# when it did.
#
# So the question a retry has to answer per payment is not "did this fail?" but
# "can we PROVE the processor never accepted an order for it?". Only then is
# re-sending safe.
RETRY_SAFE = "deterministic"
IN_DOUBT = "in_doubt"

# Failure reasons this codebase produces BEFORE any request could reach the
# processor. Everything else is in-doubt — including reasons we don't recognise
# (a future adapter, a legacy row), which is the fail-closed default.
#
# Deliberately NOT here:
#   `unexpected_error:*`      the dispatcher swallowed an exception; a read
#                             timeout after the processor accepted looks
#                             identical to one before it
#   `*_transport_error:*`     the request may have been received and actioned
#   `*_api_error:*`           the provider answered — a 5xx can still have
#                             created the order
#   `checkeeper_duplicate_suppressed`
#                             the 48h slot was already claimed, i.e. a cheque
#                             for this order was very likely already printed
#   `adapter_error:*` (cards) the card provider may have minted a card we never
#                             recorded
#   reconciler_max_age_exceeded*
#                             a genuinely `submitted` payment (real money in
#                             flight) the reconciler gave up waiting on
_RETRY_SAFE_FAILURE_PREFIXES = (
    # We refused it ourselves, before the adapter was ever called.
    "compliance_refusal:",
    "compliance_dismissed",
    "international_payment_error:",
    # Card leg: no card was minted (a provider-side `adapter_error:` reason
    # replaces this string and is deliberately absent from this list).
    "card_issuance_conflict",
    "card_issuance_failed",
    "cards_not_enabled",
    # dwolla's unsupported-method refusal: "method 'wire' is not supported…"
    "method '",
    # A credit memo landed between booking and dispatch, so the row's amount is
    # no longer what the vendor is owed. `_execute_single_payment` refuses
    # BEFORE the adapter call, so no order exists at the processor.
    "net_amount_changed",
)

# Per-adapter pre-flight refusals — checked before any HTTP call is made.
_RETRY_SAFE_FAILURE_SUFFIXES = (
    "_not_configured",
    "_no_counterparty",
    "_no_external_account",
    "_no_destination_funding_source",
    "_missing_mailing_address",
    # checkeeper: Redis was down so it refused to issue WITHOUT a dedup guard.
    "_idempotency_unavailable",
)


def classify_payment_failure(*, failure_reason: str | None, provider_payment_id: str | None) -> str:
    """Is this failed payment safe to re-attempt under a fresh idempotency key?

    Returns ``RETRY_SAFE`` only when we can prove no order was created at the
    processor; ``IN_DOUBT`` otherwise. Pure — no DB, no clock.

    A populated ``provider_payment_id`` outranks the reason entirely: every
    adapter here returns a handle only from a create call that SUCCEEDED, so
    holding one means an order exists over there and its true outcome has to be
    reconciled (or the payment voided) by a human before any re-send.
    """
    if provider_payment_id:
        return IN_DOUBT
    reason = (failure_reason or "").strip()
    if not reason:
        return IN_DOUBT
    if reason.startswith(_RETRY_SAFE_FAILURE_PREFIXES):
        return RETRY_SAFE
    if reason.endswith(_RETRY_SAFE_FAILURE_SUFFIXES):
        return RETRY_SAFE
    return IN_DOUBT


def is_retry_safe(payment: Payment) -> bool:
    """`classify_payment_failure` over a `Payment` row."""
    return (
        classify_payment_failure(
            failure_reason=payment.failure_reason,
            provider_payment_id=payment.provider_payment_id,
        )
        == RETRY_SAFE
    )


async def blocking_exception_types(
    db: AsyncSession, invoice_ids: list[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """Map each blocked invoice id → the TYPE of exception blocking it.

    The single definition of "is this invoice payable right now", shared by
    `blocked_invoice_ids` (which only needs the ids) and by the payment queue
    (which also needs to tell the operator *why* a row can't be selected). One
    query, one predicate — so the queue can never offer a row the run builder
    then refuses, and adding a type to `PAYMENT_BLOCKING_EXCEPTION_TYPES`
    updates both surfaces at once.

    An invoice carrying several blocking exceptions reports the one earliest in
    `PAYMENT_BLOCKING_EXCEPTION_TYPES` — a fixed tuple, so the answer is stable
    across calls rather than dependent on row order.

    The returned value is the exception TYPE only, never its description: it is
    rendered to an operator and travels through a JSON body, and a description
    can carry vendor / bank / amount detail.
    """
    from app.api.payments import PAYMENT_BLOCKING_EXCEPTION_TYPES

    if not invoice_ids:
        return {}
    rows = await db.execute(
        select(InvoiceException.invoice_id, InvoiceException.exception_type).where(
            InvoiceException.invoice_id.in_(invoice_ids),
            InvoiceException.exception_type.in_(PAYMENT_BLOCKING_EXCEPTION_TYPES),
            InvoiceException.status.notin_(("resolved", "dismissed")),
        )
    )
    rank = {t: i for i, t in enumerate(PAYMENT_BLOCKING_EXCEPTION_TYPES)}
    unranked = len(rank)
    out: dict[uuid.UUID, str] = {}
    for invoice_id, exception_type in rows.all():
        if invoice_id is None:
            continue
        held = out.get(invoice_id)
        if held is None or rank.get(exception_type, unranked) < rank.get(held, unranked):
            out[invoice_id] = exception_type
    return out


async def blocked_invoice_ids(db: AsyncSession, invoice_ids: list[uuid.UUID]) -> set[uuid.UUID]:
    """Which of ``invoice_ids`` carry an UNRESOLVED payment-blocking exception.

    `PAYMENT_BLOCKING_EXCEPTION_TYPES` (duplicate / fraud_flag /
    line_total_mismatch / payment_reconciliation) are `error`-severity
    financial-integrity flags that invoice approval does NOT gate on, so a run
    must refuse them and so must anything that re-dispatches money later — a
    `fraud_flag` raised between run creation and a `/retry-failed` days
    afterwards (a BEC bank-detail swap, an altered cheque off a Positive Pay
    return) has to stop the re-send. Shared by both callers precisely so they
    can't drift.
    """
    return set(await blocking_exception_types(db, invoice_ids))


async def applied_credit_total(db: AsyncSession, invoice_id: uuid.UUID) -> Decimal:
    """Sum of the credit memos already APPLIED against an invoice."""
    return (
        await db.execute(
            select(func.coalesce(func.sum(CreditMemo.amount), Decimal("0"))).where(
                CreditMemo.invoice_id == invoice_id,
                CreditMemo.status == "applied",
            )
        )
    ).scalar_one()


async def net_payable_amount(db: AsyncSession, invoice: Invoice) -> Decimal:
    """What a payment against ``invoice`` should actually move.

    Applying a credit memo is the whole point of the feature: it must reduce
    what the vendor is paid. Both money paths go through here — the payment-run
    builder below and the standalone ``POST /api/payments`` — so the two can't
    disagree about what an invoice is worth. The standalone endpoint used to
    pay ``invoice.amount`` flat and 422 any other figure, which meant a credited
    invoice paid the FULL pre-credit amount there and the correct net figure
    could not even be submitted.

    ``credit_memos.py``'s own over-application guard (apply refuses a memo that
    would exceed the invoice's remaining creditable balance) is what guarantees
    this can never go negative.
    """
    return (invoice.amount or Decimal("0")) - await applied_credit_total(db, invoice.id)


@dataclass(frozen=True)
class PaymentRunItemInput:
    invoice_id: uuid.UUID
    method: str = "ach"


@dataclass(frozen=True)
class PaymentRunCreationResult:
    run: PaymentRun
    total_amount: Decimal
    payment_count: int
    # False when an existing `plan_id` run was returned instead of a new one
    # being created — the caller uses this to pick 200 vs 201.
    created: bool


@asynccontextmanager
async def _savepoint(db: AsyncSession):
    """A savepoint around the run + payment inserts.

    Both callers need the outer transaction to stay usable after an
    ``IntegrityError`` so they can go back to the database and say something
    useful about it: the copilot path re-queries the ``plan_id`` race's winner,
    and BOTH paths re-query which invoices are actually holding the live
    payment that the ``uq_payments_one_live_per_invoice`` index rejected. Without
    the savepoint the session is poisoned at that point and the only honest
    thing left to say is "one or more invoices" — which is exactly the
    unactionable 409 this replaced.
    """
    async with db.begin_nested():
        yield


async def _live_payment_invoice_numbers(
    db: AsyncSession, invoice_ids: list[uuid.UUID]
) -> list[str]:
    """Invoice numbers among ``invoice_ids`` that already hold a LIVE payment.

    "Live" is the same definition the ``uq_payments_one_live_per_invoice``
    partial index uses — anything not in
    ``api/payments.LIVE_PAYMENT_TERMINAL_STATUSES`` — so this names exactly the
    rows that caused the insert to be rejected. Invoice NUMBER, not vendor or
    amount: it is the identifier the operator selected the row by, and it
    carries no PII.
    """
    from app.api.payments import LIVE_PAYMENT_TERMINAL_STATUSES

    if not invoice_ids:
        return []
    rows = await db.execute(
        select(Invoice.invoice_number)
        .join(Payment, Payment.invoice_id == Invoice.id)
        .where(
            Invoice.id.in_(invoice_ids),
            Payment.status.notin_(LIVE_PAYMENT_TERMINAL_STATUSES),
        )
        .distinct()
    )
    return sorted(n for n in rows.scalars().all() if n)


async def _existing_run_for_plan(db: AsyncSession, plan_id: str) -> PaymentRunCreationResult | None:
    existing = (
        await db.execute(select(PaymentRun).where(PaymentRun.plan_id == plan_id))
    ).scalar_one_or_none()
    if existing is None:
        return None
    count = (
        await db.execute(select(func.count()).where(Payment.payment_run_id == existing.id))
    ).scalar() or 0
    return PaymentRunCreationResult(
        run=existing,
        total_amount=existing.total_amount or Decimal("0"),
        payment_count=count,
        created=False,
    )


async def create_payment_run_for_invoices(
    db: AsyncSession,
    *,
    org: Organization,
    org_id: uuid.UUID,
    entity_id: uuid.UUID,
    scope_entity_id: uuid.UUID | None,
    user: User,
    items: list[PaymentRunItemInput],
    plan_id: str | None = None,
) -> PaymentRunCreationResult:
    """Validate + create a draft ``PaymentRun`` for ``items``.

    Runs the identical gates ``POST /api/payments/runs`` runs: payable-status
    (``PAYABLE_INVOICE_STATUSES``), the financial-integrity block (every member
    of ``PAYMENT_BLOCKING_EXCEPTION_TYPES`` — named by the tuple, not spelled
    out, so this docstring can't fall behind it the way the 409 message did),
    credit-memo netting, and the CFO-approval-threshold computation. NEVER
    executes the run — it always lands ``status="draft"``.

    ``entity_id`` is the entity newly-created rows are STAMPED with (the
    caller's ``get_write_entity_id``, never ``None``). ``scope_entity_id`` is
    the entity the caller has SELECTED (``get_entity_id``, ``None`` in the
    consolidated view) and is what the invoice lookup is filtered by — the two
    are deliberately separate, exactly as they are on ``POST /api/payments``.
    Without the scope filter an operator with subsidiary B selected could stage
    a run over subsidiary A's invoices: the run landed under B (visible and
    executable from B's queue) while each payment was stamped with A's entity,
    so executing it moved A's money from B's screen — on the one route in this
    module that books it. An out-of-scope id is the same opaque
    "One or more invoices not found" as a missing one, so the response can't
    enumerate another entity's invoices (same posture as
    ``api/payments.py::_get_scoped_payment``).

    ``plan_id``, when given, is a correlation-key idempotency anchor (the AI
    Cash-Flow Copilot's deterministic plan id —
    ``services/cash_flow_plan.compute_plan_id``): if a run already exists for
    this ``plan_id``, it is returned as-is (``created=False``) instead of a
    second one being staged — the copilot's draft-run enact endpoint is safe
    to retry. Manual runs from ``POST /api/payments/runs`` never pass a
    ``plan_id``, so that caller's behavior is unchanged.

    Raises ``HTTPException`` (404/409) on any validation failure, same status
    codes the original inline implementation used.
    """
    # Local import — avoids a module-level import cycle (api/payments.py
    # imports THIS module for the create function; this module needs
    # api/payments.py's shared status/exception-type constants). Mirrors the
    # existing lazy-import pattern api/cards.py already uses for the same
    # constant.
    from app.api.payments import PAYABLE_INVOICE_STATUSES

    if not items:
        raise HTTPException(status_code=422, detail="At least one invoice is required")

    if plan_id is not None:
        existing_result = await _existing_run_for_plan(db, plan_id)
        if existing_result is not None:
            return existing_result

    invoice_ids = [item.invoice_id for item in items]
    result = await db.execute(
        apply_entity_scope(
            select(Invoice).where(Invoice.id.in_(invoice_ids)), Invoice, scope_entity_id
        )
    )
    invoices = {inv.id: inv for inv in result.scalars().all()}

    if len(invoices) != len(invoice_ids):
        raise HTTPException(status_code=404, detail="One or more invoices not found")

    # Refuse a run spanning more than one currency. `PaymentRun.total_amount`
    # (and the CFO-threshold comparison below) is a single bare `Numeric`
    # column with no currency of its own — summing a USD invoice and a EUR
    # invoice into it would produce a number that isn't denominated in
    # anything real, and could misfire (or fail to fire) the CFO gate on a
    # face-value coincidence across currencies. Each `Payment` still settles
    # independently in its own invoice's currency at execution time
    # (`_execute_single_payment` reads `invoice.currency` per payment) — this
    # only constrains what one BATCH can report a single total for.
    currencies = {invoices[iid].currency or "USD" for iid in invoice_ids}
    if len(currencies) > 1:
        raise HTTPException(
            status_code=422,
            detail=(
                "All invoices in a payment run must share the same currency "
                f"(found: {', '.join(sorted(currencies))})."
            ),
        )

    not_payable = [
        inv.invoice_number
        for inv in invoices.values()
        if inv.status not in PAYABLE_INVOICE_STATUSES
    ]
    if not_payable:
        raise HTTPException(
            status_code=409,
            detail=f"Invoice(s) not approved for payment: {', '.join(not_payable)}",
        )

    # Name the type that ACTUALLY blocked each invoice rather than reciting a
    # hardcoded list of causes. The list said "duplicate/fraud/line-total" while
    # `PAYMENT_BLOCKING_EXCEPTION_TYPES` had since grown `payment_reconciliation`
    # — so an invoice held back because a payment may still be in flight at the
    # rail was refused with a message naming three exceptions it doesn't carry,
    # sending the operator to clear something that isn't there. Deriving the
    # reason from the same map the queue's `blocked_reason` uses retires the
    # whole drift class, not just this instance. The exception TYPE is a fixed
    # PII-free vocabulary; the description (which can carry vendor / bank /
    # amount detail) is never included — see `blocking_exception_types`.
    blocked = await blocking_exception_types(db, invoice_ids)
    if blocked:
        blocked_numbers = sorted(
            f"{inv.invoice_number} ({blocked[iid]})"
            for iid, inv in invoices.items()
            if iid in blocked
        )
        raise HTTPException(
            status_code=409,
            detail=(
                "Invoice(s) have an unresolved payment-blocking exception and "
                f"can't be paid until it's cleared: {', '.join(blocked_numbers)}"
            ),
        )

    # Net any applied credit memos off what actually gets paid.
    #
    # Two totals, deliberately: `total` is what the run PAYS, in the one
    # currency its invoices share (`PaymentRun.total_amount`), and
    # `reporting_total` is the same money expressed in the org's REPORTING
    # currency, which is the only currency the CFO threshold is denominated in.
    # They are equal for a domestic run and differ for a foreign one — which is
    # exactly the case the gate used to get wrong.
    reporting_currency = resolve_reporting_currency(org.settings)
    net_amounts: dict[uuid.UUID, Decimal] = {}
    total = Decimal("0")
    reporting_total = Decimal("0")
    reporting_unconverted = False
    for item in items:
        inv = invoices[item.invoice_id]
        net_amount = await net_payable_amount(db, inv)
        net_amounts[item.invoice_id] = net_amount
        total += net_amount
        # No FX call: the rate was locked onto the invoice row when it was last
        # saved (`currency_conversion.materialize_reporting_amount`). A row we
        # can't price in the reporting currency flags `unconverted`, which the
        # gate below reads as fail-closed.
        reported, unconverted = reporting_amount_at_locked_rate(
            amount=net_amount,
            currency=inv.currency,
            reporting_currency=reporting_currency,
            persisted_reporting_currency=inv.reporting_currency,
            persisted_reporting_source_currency=inv.reporting_source_currency,
            persisted_fx_rate=inv.reporting_fx_rate,
        )
        reporting_total += reported
        reporting_unconverted = reporting_unconverted or unconverted

    # An invoice fully covered by applied credit memos has nothing to pay. The
    # standalone `POST /api/payments` already refuses this; staging it into a
    # run instead booked a $0 payment, which a real rail rejects as `failed` —
    # leaving the invoice stuck in the payable queue with no exit that
    # recognises "there is nothing to move" (and, on `virtual_card`, minting a
    # $0 card at the provider first). Both money paths refuse identically.
    fully_credited = [
        invoices[item.invoice_id].invoice_number
        for item in items
        if net_amounts[item.invoice_id] <= 0
    ]
    if fully_credited:
        raise HTTPException(
            status_code=409,
            detail=(
                "Invoice(s) fully covered by applied credit memos — nothing to pay: "
                f"{', '.join(sorted(fully_credited))}"
            ),
        )

    # CFO sign-off threshold. Compared against the REPORTING-currency figure,
    # not the run's own-currency total — the threshold is a bare number in the
    # org's reporting currency, so a GBP 9,000 run used to slip under a USD
    # 10,000 gate. Fail-closed on an unparseable threshold and on an amount we
    # can't express in that currency; `services/payment_controls` owns both
    # rules so this path and the standalone `POST /api/payments` can't drift.
    pmt_cfg = (org.settings or {}).get("payments") or {}
    cfo = cfo_approval_decision(
        payment_config=pmt_cfg,
        reporting_amount=reporting_total,
        reporting_currency=reporting_currency,
        unconverted=reporting_unconverted,
    )
    requires_cfo = cfo.required
    if cfo.reason == CFO_REASON_THRESHOLD_UNPARSEABLE:
        logger.error(
            "payments.cfo_approval_above is unparseable (%r) for org %s; "
            "requiring CFO approval on this run (fail-closed)",
            pmt_cfg.get("cfo_approval_above"),
            org.id,
        )
    elif cfo.reason == CFO_REASON_AMOUNT_NOT_EXPRESSIBLE:
        logger.warning(
            "payment run for org %s could not be expressed in %s (no locked rate on "
            "one or more invoices); requiring CFO approval (fail-closed)",
            org.id,
            reporting_currency,
        )

    run = PaymentRun(
        organization_id=org_id,
        entity_id=entity_id,
        status="draft",
        total_amount=total,
        initiated_by=user.id,
        requires_cfo_approval=requires_cfo,
        plan_id=plan_id,
    )

    # A savepoint around the insert: on IntegrityError (the
    # payments-per-invoice backstop, OR — when `plan_id` is set — a concurrent
    # duplicate draft-run request racing this one for the SAME plan_id via the
    # partial unique index on payment_runs.plan_id) the outer transaction has
    # to stay usable so we can go back to the database, both to re-query the
    # plan_id race's winner and to NAME the invoices already holding a live
    # payment. Mirrors api/payments.py::create_payment's savepoint idiom.
    try:
        async with _savepoint(db):
            db.add(run)
            await db.flush()
            for item in items:
                inv = invoices[item.invoice_id]
                payment = Payment(
                    invoice_id=inv.id,
                    entity_id=inv.entity_id,
                    payment_run_id=run.id,
                    amount=net_amounts[item.invoice_id],
                    method=item.method,
                    status="pending",
                    correlation_id=uuid.uuid4(),
                )
                db.add(payment)
            await db.flush()
    except IntegrityError as exc:
        if plan_id is not None:
            existing_result = await _existing_run_for_plan(db, plan_id)
            if existing_result is not None:
                return existing_result
        # Name the offending invoices. "One or more invoices already have a
        # live payment scheduled" identified nothing: on a 40-invoice run the
        # operator had no way to tell which row to drop, and the only route
        # forward was bisecting the selection by hand.
        blocked = await _live_payment_invoice_numbers(db, invoice_ids)
        if blocked:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Invoice(s) already have a live payment scheduled — remove them from "
                    f"the run, or void the existing payment first: {', '.join(blocked)}"
                ),
            ) from exc
        raise HTTPException(
            status_code=409,
            detail="One or more invoices already have a live payment scheduled.",
        ) from exc

    # Local import (not top-level): callers/tests patch
    # `app.services.audit_dispatch.dispatch_audit` — a module-level import
    # here would bind the reference before the patch lands and silently not
    # observe it. Matches the original inline implementation's own pattern.
    from app.services.audit_dispatch import dispatch_audit

    await dispatch_audit(
        db,
        correlation_id=uuid.uuid4(),
        organization_id=org.id,
        actor_id=user.id,
        action="payment_run.created",
        entity_type="payment_run",
        entity_id=run.id,
        details={
            "total_amount": str(total),
            "payment_count": len(items),
            "requires_cfo_approval": run.requires_cfo_approval,
            "plan_id": plan_id,
            # WHAT the CFO gate compared. For a foreign-currency run this is
            # not `total_amount`, so without it the audit row cannot explain
            # why a GBP 9,000 run required sign-off against a 10,000 threshold.
            # PII-free — amounts, a currency code and a fixed reason vocabulary.
            "cfo_threshold_currency": cfo.currency,
            "cfo_evaluated_amount": (
                None if cfo.evaluated_amount is None else str(cfo.evaluated_amount)
            ),
            "cfo_reason": cfo.reason,
        },
    )

    return PaymentRunCreationResult(
        run=run, total_amount=total, payment_count=len(items), created=True
    )
