"""Exception lifecycle — the single chokepoint for queue decisions + their
append-only audit rows.

An AP ``Exception`` is a *control*, not a note. Three of the types the queue
carries — ``duplicate``, ``fraud_flag``, ``line_total_mismatch`` — block a
payment run outright (``api/payments.PAYMENT_BLOCKING_EXCEPTION_TYPES``), and
invoice approval gates on none of them. Clearing one is therefore the human
sign-off that lets money move, and it has to leave a trace an auditor can trust.

The ``exceptions`` table cannot be that trace: it is mutable, and every
resolution overwrites the last (``status`` / ``resolution`` / ``resolved_by`` /
``resolved_at`` are single-valued), so an escalate-then-resolve loses the first
decider entirely. It is also not shipped to the SOC 2 WORM store — only
``audit_log`` is, and only ``audit_log`` carries the DB-level append-only
trigger (migration ``0022_sox_audit_immutable``).

So every lifecycle event writes an ``audit_log`` row through here:

| Action                 | Written when                                        |
|------------------------|-----------------------------------------------------|
| ``exception.raised``   | ``exception_service.create_exception`` opens a row   |
| ``exception.resolved`` | a human or an agent resolves it                      |
| ``exception.escalated``| a human or an agent escalates it                     |
| ``exception.dismissed``| a human dismisses it                                 |
| ``exception.assigned`` | the queue routes it to (or away from) a user         |

Rows are **correlation-keyed to the invoice**, so they land on the invoice's own
SOX trail (``GET /api/audit/invoice/{id}`` and the auditor export both select on
``correlation_id``) alongside ``invoice.approved`` / ``invoice.rejected``. An
invoice-less exception — a Positive Pay ``not_on_file`` cheque the bank cleared
that we never issued — has no invoice correlation, so it uses its own id, which
still groups that exception's raise/assign/resolve rows together.

``details`` stays lean and free of regulated values: ids, the type, the
severity, the status delta, and the ``payment_blocking`` flag that tells an
auditor this decision unblocked money. The human's justification is carried as
``resolution`` (truncated) for the same reason ``invoice.rejected`` carries its
``reason`` — it is the decision's rationale, and the mutable row it also lives
on can be overwritten. The exception ``description`` is deliberately NOT copied:
it is generated text that can name a vendor, the row already holds it, and the
audit trail gains nothing by duplicating it.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.exception import Exception as APException
from app.models.invoice import Invoice
from app.services.audit_dispatch import dispatch_audit

ACTION_RAISED = "exception.raised"
ACTION_RESOLVED = "exception.resolved"
ACTION_ESCALATED = "exception.escalated"
ACTION_DISMISSED = "exception.dismissed"
ACTION_ASSIGNED = "exception.assigned"

#: Queue verb → terminal/queue status the verb produces.
RESOLUTION_STATUSES: dict[str, str] = {
    "resolve": "resolved",
    "escalate": "escalated",
    "dismiss": "dismissed",
}

#: Queue verb → the audit action it writes.
RESOLUTION_ACTIONS: dict[str, str] = {
    "resolve": ACTION_RESOLVED,
    "escalate": ACTION_ESCALATED,
    "dismiss": ACTION_DISMISSED,
}

#: Statuses an exception can still be acted on from.
ACTIONABLE_STATUSES = ("open", "escalated")

#: Cap on the free-text justification copied into the immutable row. The
#: exception row keeps the full text; the audit row only needs enough to
#: reconstruct the decision, and an unbounded blob in JSONB is a footgun.
_MAX_RESOLUTION_CHARS = 500


def is_payment_blocking(exception_type: str) -> bool:
    """True when an unresolved exception of this type stops a payment run.

    Reads ``api/payments.PAYMENT_BLOCKING_EXCEPTION_TYPES`` (the one definition;
    ``services/payment_runs`` imports it the same lazy way) so the audit row's
    ``payment_blocking`` flag can never drift from the gate it describes.
    """
    from app.api.payments import PAYMENT_BLOCKING_EXCEPTION_TYPES

    return exception_type in PAYMENT_BLOCKING_EXCEPTION_TYPES


def apply_resolution(
    exc: APException,
    action: str,
    resolution: str,
    actor_name: str,
    *,
    now: datetime | None = None,
) -> str:
    """Mutate ``exc`` for a queue ``action`` and return the resulting status.

    Pure bookkeeping — no I/O, no audit row, no commit. Callers that need the
    audit row use :func:`record_decision`, which wraps this.

    ``escalate`` is **not** a resolution. It records the decision note (so the
    human picking the escalation up reads why it was raised) but leaves
    ``resolved_by`` / ``resolved_at`` / ``time_to_resolution_seconds`` alone —
    a still-open row that advertises a resolver and a resolution timestamp is
    exactly the kind of thing that misleads an auditor, and the SLA clock is
    still running. Who escalated, and when, is on the immutable
    ``exception.escalated`` audit row instead, which is the right place for it.
    ``time_to_resolution`` is therefore computed once, on the trip to a genuinely
    terminal state (resolve / dismiss).

    Raises ``ValueError`` on an unknown action — the API layer maps that to 400.
    """
    new_status = RESOLUTION_STATUSES.get(action)
    if new_status is None:
        raise ValueError(f"Unknown action: {action}")

    stamp = now or datetime.now(UTC)
    exc.status = new_status
    exc.resolution = resolution
    if action == "escalate":
        return new_status

    exc.resolved_by = actor_name
    exc.resolved_at = stamp
    if exc.created_at is not None:
        exc.time_to_resolution_seconds = int((stamp - exc.created_at).total_seconds())
    return new_status


async def _correlation_id(
    db: AsyncSession,
    exception: APException,
    invoice: Invoice | None = None,
) -> uuid.UUID:
    """Resolve the correlation the audit row files under.

    The invoice's correlation when there is one (so the row joins that
    invoice's SOX trail), else the exception's own id — which still groups an
    invoice-less exception's own events together.
    """
    correlation = getattr(invoice, "correlation_id", None)
    if correlation:
        return correlation
    if exception.invoice_id is not None:
        found = (
            await db.execute(
                select(Invoice.correlation_id).where(Invoice.id == exception.invoice_id)
            )
        ).scalar_one_or_none()
        if found:
            return found
    return exception.id


async def correlation_ids_for(
    db: AsyncSession,
    exceptions: Sequence[APException],
) -> dict[uuid.UUID, uuid.UUID]:
    """``{exception_id: correlation_id}`` for a batch, in ONE query.

    Bulk callers (``POST /api/exceptions/bulk/resolve``) pass the result through
    to :func:`record_decision` so a 200-row bulk action doesn't fire 200 extra
    correlation lookups. Same rule as the single-row path: the invoice's
    correlation when there is one, else the exception's own id.
    """
    invoice_ids = {e.invoice_id for e in exceptions if e.invoice_id is not None}
    by_invoice: dict[uuid.UUID, uuid.UUID] = {}
    if invoice_ids:
        rows = (
            await db.execute(
                select(Invoice.id, Invoice.correlation_id).where(Invoice.id.in_(invoice_ids))
            )
        ).all()
        by_invoice = {inv_id: corr for inv_id, corr in rows if corr}
    return {
        e.id: (by_invoice.get(e.invoice_id) if e.invoice_id else None) or e.id for e in exceptions
    }


def _base_details(exception: APException) -> dict:
    return {
        "exception_id": str(exception.id),
        "exception_type": exception.exception_type,
        "severity": exception.severity,
        "invoice_id": str(exception.invoice_id) if exception.invoice_id else None,
        "payment_blocking": is_payment_blocking(exception.exception_type),
    }


async def _write(
    db: AsyncSession,
    *,
    exception: APException,
    invoice: Invoice | None,
    action: str,
    actor_id: uuid.UUID | None,
    details: dict,
    correlation_id: uuid.UUID | None = None,
) -> None:
    await dispatch_audit(
        db,
        correlation_id=correlation_id or await _correlation_id(db, exception, invoice),
        organization_id=exception.organization_id,
        actor_id=actor_id,
        action=action,
        entity_type="exception",
        entity_id=exception.id,
        details=details,
    )


async def record_raised(
    db: AsyncSession,
    *,
    exception: APException,
    invoice: Invoice | None = None,
    actor_id: uuid.UUID | None = None,
) -> None:
    """Write the ``exception.raised`` row. Called from the create chokepoint.

    ``actor_id`` is normally ``None`` — nearly every exception is opened by a
    detector (duplicate / fraud / PO-mismatch / line-total) rather than by a
    person, and a fabricated actor would be worse than an honest null.
    """
    details = _base_details(exception)
    details["new_status"] = exception.status
    await _write(
        db,
        exception=exception,
        invoice=invoice,
        action=ACTION_RAISED,
        actor_id=actor_id,
        details=details,
    )


async def record_decision(
    db: AsyncSession,
    *,
    exception: APException,
    action: str,
    resolution: str,
    actor_id: uuid.UUID | None,
    actor_name: str,
    invoice: Invoice | None = None,
    via: str | None = None,
    correlation_id: uuid.UUID | None = None,
) -> str:
    """Apply a queue decision AND write its append-only audit row.

    The single path both the human queue (``api/exceptions``) and the
    autonomous agents (``services/exception_agents/coordinator``) take, so the
    two can't drift on either the bookkeeping or the trail. Returns the new
    status. Does not commit — the caller owns the transaction.

    ``via`` marks a non-interactive decider (``"agent"``); the row's
    ``actor_id`` still names the human who triggered the run.
    ``correlation_id`` lets a bulk caller supply a pre-resolved correlation (see
    :func:`correlation_ids_for`) instead of paying a lookup per row.
    """
    old_status = exception.status
    new_status = apply_resolution(exception, action, resolution, actor_name)

    details = _base_details(exception)
    details["old_status"] = old_status
    details["new_status"] = new_status
    if resolution:
        details["resolution"] = resolution[:_MAX_RESOLUTION_CHARS]
    if exception.time_to_resolution_seconds is not None:
        details["time_to_resolution_seconds"] = exception.time_to_resolution_seconds
    if via:
        details["via"] = via

    await _write(
        db,
        exception=exception,
        invoice=invoice,
        action=RESOLUTION_ACTIONS[action],
        actor_id=actor_id,
        details=details,
        correlation_id=correlation_id,
    )
    return new_status


async def record_assignment(
    db: AsyncSession,
    *,
    exception: APException,
    assigned_to_user_id: uuid.UUID | None,
    actor_id: uuid.UUID | None,
    invoice: Invoice | None = None,
) -> None:
    """Write the ``exception.assigned`` row (``None`` assignee = unassigned).

    Only the assignee's **id** is recorded — the display name is resolvable from
    the control plane and doesn't belong duplicated in the trail.
    """
    details = _base_details(exception)
    details["assigned_to_user_id"] = str(assigned_to_user_id) if assigned_to_user_id else None
    await _write(
        db,
        exception=exception,
        invoice=invoice,
        action=ACTION_ASSIGNED,
        actor_id=actor_id,
        details=details,
    )
