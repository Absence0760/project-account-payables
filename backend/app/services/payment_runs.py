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

        Preserves the exact precedence the dispatcher has always used: all-fail
        → `failed`, any-fail-with-a-survivor → `partial`, anything still in
        flight → `submitted`, otherwise `completed`.
        """
        if self.failed and not (self.completed or self.in_flight):
            return "failed"
        if self.failed:
            return "partial"
        if self.in_flight:
            return "submitted"
        return "completed"


def rollup_payment_statuses(statuses: Iterable[str | None]) -> PaymentRunRollup:
    """Bucket a run's payment statuses into a `PaymentRunRollup`."""
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
async def _maybe_savepoint(db: AsyncSession, *, use_savepoint: bool):
    """``db.begin_nested()`` when ``use_savepoint``, otherwise a plain no-op.

    Only the ``plan_id``-bearing (copilot) path needs the savepoint — it's
    what keeps the outer transaction usable after an ``IntegrityError`` so
    the plan_id race can be re-queried and its winner returned (see
    ``create_payment_run_for_invoices``). The plain manual-run path
    (``plan_id=None``, ``POST /api/payments/runs``) never re-queries after a
    failed flush — it just raises — so it keeps its original unwrapped shape
    exactly, unchanged from before this module existed.
    """
    if use_savepoint:
        async with db.begin_nested():
            yield
    else:
        yield


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
    user: User,
    items: list[PaymentRunItemInput],
    plan_id: str | None = None,
) -> PaymentRunCreationResult:
    """Validate + create a draft ``PaymentRun`` for ``items``.

    Runs the identical gates ``POST /api/payments/runs`` runs: payable-status
    (``PAYABLE_INVOICE_STATUSES``), the duplicate/fraud/line-total-mismatch
    financial-integrity block (``PAYMENT_BLOCKING_EXCEPTION_TYPES``),
    credit-memo netting, and the CFO-approval-threshold computation. NEVER
    executes the run — it always lands ``status="draft"``.

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
    from app.api.payments import PAYABLE_INVOICE_STATUSES, PAYMENT_BLOCKING_EXCEPTION_TYPES

    if not items:
        raise HTTPException(status_code=422, detail="At least one invoice is required")

    if plan_id is not None:
        existing_result = await _existing_run_for_plan(db, plan_id)
        if existing_result is not None:
            return existing_result

    invoice_ids = [item.invoice_id for item in items]
    result = await db.execute(select(Invoice).where(Invoice.id.in_(invoice_ids)))
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

    blocking_res = await db.execute(
        select(InvoiceException.invoice_id).where(
            InvoiceException.invoice_id.in_(invoice_ids),
            InvoiceException.exception_type.in_(PAYMENT_BLOCKING_EXCEPTION_TYPES),
            InvoiceException.status.notin_(("resolved", "dismissed")),
        )
    )
    blocked_ids = set(blocking_res.scalars().all())
    if blocked_ids:
        blocked_numbers = [
            inv.invoice_number for iid, inv in invoices.items() if iid in blocked_ids
        ]
        raise HTTPException(
            status_code=409,
            detail=(
                "Invoice(s) have an unresolved duplicate/fraud/line-total exception and "
                f"can't be paid until it's cleared: {', '.join(sorted(blocked_numbers))}"
            ),
        )

    # Net any applied credit memos off what actually gets paid.
    net_amounts: dict[uuid.UUID, Decimal] = {}
    total = Decimal("0")
    for item in items:
        inv = invoices[item.invoice_id]
        already_applied = (
            await db.execute(
                select(func.coalesce(func.sum(CreditMemo.amount), Decimal("0"))).where(
                    CreditMemo.invoice_id == inv.id,
                    CreditMemo.status == "applied",
                )
            )
        ).scalar_one()
        net_amount = inv.amount - already_applied
        net_amounts[item.invoice_id] = net_amount
        total += net_amount

    # CFO sign-off threshold — identical fail-closed handling of a corrupted /
    # unparseable setting as the original inline implementation.
    pmt_cfg = (org.settings or {}).get("payments") or {}
    cfo_threshold_raw = pmt_cfg.get("cfo_approval_above")
    requires_cfo = False
    if cfo_threshold_raw is not None:
        try:
            cfo_threshold = Decimal(str(cfo_threshold_raw))
        except (ValueError, ArithmeticError):
            logger.error(
                "payments.cfo_approval_above is unparseable (%r) for org %s; "
                "requiring CFO approval on this run (fail-closed)",
                cfo_threshold_raw,
                org.id,
            )
            requires_cfo = True
        else:
            if cfo_threshold > 0 and total > cfo_threshold:
                requires_cfo = True

    run = PaymentRun(
        organization_id=org_id,
        entity_id=entity_id,
        status="draft",
        total_amount=total,
        initiated_by=user.id,
        requires_cfo_approval=requires_cfo,
        plan_id=plan_id,
    )

    # A savepoint around the insert (copilot path only — see
    # `_maybe_savepoint`): on IntegrityError (the payments-per-invoice
    # backstop, OR — when `plan_id` is set — a concurrent duplicate
    # draft-run request racing this one for the SAME plan_id via the partial
    # unique index on payment_runs.plan_id) we need the outer transaction to
    # stay usable so we can re-query for the plan_id race's winner. Mirrors
    # api/payments.py::create_payment's savepoint idiom.
    try:
        async with _maybe_savepoint(db, use_savepoint=plan_id is not None):
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
        },
    )

    return PaymentRunCreationResult(
        run=run, total_amount=total, payment_count=len(items), created=True
    )
