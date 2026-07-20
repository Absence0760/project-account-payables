"""Recurring / subscription invoice generation — scheduler + sweep + variance.

Predictable, fixed-cadence spend (rent, SaaS seats, utilities, insurance)
shouldn't need a fresh upload + extraction every period. A
:class:`~app.models.recurring_invoice.RecurringInvoiceTemplate` captures the
vendor, amount, GL coding, entity and a simple cadence (monthly / quarterly /
annual + day-of-period); this module turns that into a calendar and generates
the next ``Invoice`` on schedule — pre-coded from the template and dropped
straight into the approval queue (``ready_for_review``), bypassing extraction.

Two surfaces share these helpers:

  * the ``/api/recurring`` router (manual ``generate-now``, the projected
    upcoming schedule, ``next_run_on`` recompute on create/edit/resume), and
  * the :func:`run_recurring_invoices_loop` background sweep, which mirrors
    ``discount_auto_trigger`` exactly — control-plane enumerates orgs, a fresh
    per-tenant engine is disposed in ``finally``, and one tenant's failure is
    logged but never halts the sweep.

Money-path boundary
-------------------
This module ONLY creates an invoice in the queue. It never schedules a payment
or moves money — a human still approves + funds each generated invoice through
the normal CFO-gated path. The worst a bug here can do is drop an extra draft
invoice into the review queue.

Idempotency
-----------
Lives at the DB layer, not here: every generated invoice carries
``Invoice.recurring_template_id`` + ``Invoice.recurring_period_key`` and a
partial unique index ``uq_invoice_recurring_period`` on that pair. A
concurrent / retried double-fire of the same period raises ``IntegrityError``
on the second INSERT, which :func:`generate_one` catches (inside a savepoint so
the surrounding sweep keeps its other work) and turns into a no-op that returns
the already-existing invoice.

All money is ``Decimal`` (never float). Only stdlib date math — no
``python-dateutil`` dependency. See ``backend/docs/recurring-invoices.md``.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.database import _make_tenant_url, control_session_factory
from app.models.invoice import Invoice, InvoiceStatus
from app.models.organization import Organization
from app.models.recurring_invoice import (
    CADENCE_ANNUAL,
    CADENCE_QUARTERLY,
    STATUS_ACTIVE,
    RecurringInvoiceTemplate,
)
from app.services.audit_dispatch import dispatch_audit
from app.services.workflow_engine import create_workflow_instance

logger = logging.getLogger(__name__)

# Platform-default variance tolerance (percent) for flagging an arrived invoice
# whose amount drifts from its recurring template. Mirrors the price-variance
# tolerance shape in vendor_enrichment (PRICE_TOLERANCE_PCT). Per-template
# override on `RecurringInvoiceTemplate.variance_tolerance_pct`.
DEFAULT_VARIANCE_TOLERANCE_PCT = Decimal("10.0")

_HUNDRED = Decimal("100")


# --------------------------------------------------------------------------- #
# Pure date math — no dateutil
# --------------------------------------------------------------------------- #


def _add_months(d: date, months: int) -> date:
    """Return ``d`` advanced by ``months`` calendar months.

    Pure stdlib. The day is clamped to the last valid day of the target month
    so e.g. Jan-31 + 1 month → Feb-28/29. Callers here only ever pass
    ``day_of_period`` clamped to 1..28 by the schema, so the clamp is belt-and-
    suspenders, but it keeps the helper correct for any input.
    """
    total = (d.year * 12 + (d.month - 1)) + months
    year, month0 = divmod(total, 12)
    month = month0 + 1
    # Last day of the target month: day-0 of the following month.
    if month == 12:
        last_day = 31
    else:
        last_day = (date(year, month + 1, 1) - date(year, month, 1)).days
    return date(year, month, min(d.day, last_day))


def _months_per_period(cadence: str) -> int:
    if cadence == CADENCE_QUARTERLY:
        return 3
    if cadence == CADENCE_ANNUAL:
        return 12
    return 1  # monthly (default)


def period_key_for(cadence: str, run_on: date) -> str:
    """Canonical period key for a generation date.

    - monthly   → ``"YYYY-MM"``
    - quarterly → ``"YYYY-Qn"`` (n = 1..4)
    - annual    → ``"YYYY"``
    """
    if cadence == CADENCE_ANNUAL:
        return f"{run_on.year:04d}"
    if cadence == CADENCE_QUARTERLY:
        quarter = (run_on.month - 1) // 3 + 1
        return f"{run_on.year:04d}-Q{quarter}"
    return f"{run_on.year:04d}-{run_on.month:02d}"


def compute_next_run_on(
    cadence: str,
    day_of_period: int,
    *,
    after: date,
    start_date: date,
    end_date: date | None = None,
) -> date | None:
    """First occurrence on/after ``max(after, start_date)`` for the cadence.

    The schedule is anchored at ``start_date``'s month/quarter/year, landing on
    ``day_of_period`` of each period (already clamped 1..28 by the schema).
    Returns the first such date that is ``>= max(after, start_date)``, or
    ``None`` if that date would fall past ``end_date``.

    Used both to seed ``next_run_on`` at create time (``after == start_date``)
    and to re-anchor it on resume so a paused template never back-fires every
    historic period it slept through (``after == today``).
    """
    floor = max(after, start_date)
    months = _months_per_period(cadence)

    # Anchor on the start period's day_of_period, then walk forward in whole
    # periods until we land on/after the floor.
    candidate = start_date.replace(day=1)
    candidate = _add_months(candidate, 0)
    candidate = date(candidate.year, candidate.month, day_of_period)
    # Guard the loop: bounded by the gap between floor and start in periods.
    while candidate < floor:
        candidate = _add_months(candidate, months)
        candidate = date(candidate.year, candidate.month, day_of_period)

    if end_date is not None and candidate > end_date:
        return None
    return candidate


def current_due_run_on(
    cadence: str,
    day_of_period: int,
    *,
    today: date,
    start_date: date,
) -> date:
    """The latest scheduled occurrence on/before ``max(today, start_date)``.

    This is the period a manual ``generate-now`` targets — the "current due"
    period. It is a pure function of (schedule, today), NOT of the mutable
    ``next_run_on`` cursor, so re-calling on the same day always resolves to the
    SAME period — which is what makes ``generate-now`` idempotent regardless of
    how far the cursor has since advanced.

    Before ``start_date`` the schedule hasn't begun, so the first occurrence
    (``start_date``'s ``day_of_period``) is returned as the floor.
    """
    months = _months_per_period(cadence)
    first = date(start_date.year, start_date.month, day_of_period)
    ceiling = max(today, start_date)
    candidate = first
    if candidate > ceiling:
        return first
    nxt = candidate
    while True:
        following = _add_months(nxt, months)
        following = date(following.year, following.month, day_of_period)
        if following > ceiling:
            break
        nxt = following
    return nxt


def project_schedule(
    template: RecurringInvoiceTemplate, *, count: int, from_date: date | None = None
) -> list[tuple[str, date]]:
    """Project the next ``count`` (period_key, run_on) occurrences forward.

    Read-only — creates nothing. Starts at ``template.next_run_on`` (or
    ``from_date`` when given) and walks the cadence, stopping early at
    ``end_date``.
    """
    if count <= 0:
        return []
    start = from_date or template.next_run_on
    if start is None:
        return []
    months = _months_per_period(template.cadence)
    out: list[tuple[str, date]] = []
    run_on = date(start.year, start.month, template.day_of_period)
    if run_on < start:
        run_on = _add_months(run_on, months)
        run_on = date(run_on.year, run_on.month, template.day_of_period)
    for _ in range(count):
        if template.end_date is not None and run_on > template.end_date:
            break
        out.append((period_key_for(template.cadence, run_on), run_on))
        run_on = _add_months(run_on, months)
        run_on = date(run_on.year, run_on.month, template.day_of_period)
    return out


# --------------------------------------------------------------------------- #
# Generation
# --------------------------------------------------------------------------- #


def _generated_invoice_number(period_key: str) -> str:
    """Deterministic-prefix, collision-resistant generated invoice number.

    ``REC-<period_key>-<short>`` where ``<short>`` is a random hex tail. The
    DB idempotency key is (template_id, period_key) — NOT this number — so a
    short random tail is fine and keeps the human-facing number unique even if
    a template is reactivated for a re-numbered run.
    """
    return f"REC-{period_key}-{uuid.uuid4().hex[:6]}"


async def generate_one(
    db: AsyncSession,
    template: RecurringInvoiceTemplate,
    *,
    run_on: date,
    actor_id: uuid.UUID | None = None,
) -> Invoice | None:
    """Generate one pre-coded invoice for ``template`` for the ``run_on`` period.

    Builds the Invoice from the template's denormalised coding, stamps the
    ``(recurring_template_id, recurring_period_key)`` idempotency pair, creates
    the workflow instance, and drops the invoice into the approval queue at
    ``ready_for_review`` (NOT ``pending`` — that is the extraction state the
    reaper sweeps; a recurring invoice is already coded and needs only
    approval). Advances the template's scheduling cursor.

    Idempotent: a concurrent / retried call for an already-generated period
    hits the partial unique index, the INSERT raises ``IntegrityError`` inside
    a savepoint, and we return the already-existing invoice (no duplicate, no
    audit, no cursor advance).

    Returns the created (or pre-existing) :class:`Invoice`. ``None`` only when
    the template lacks the minimum data to generate (no vendor / no amount).
    """
    if template.amount is None or not template.vendor_name:
        logger.warning(
            "[recurring] template %s not generatable (missing amount/vendor)", template.id
        )
        return None

    period_key = period_key_for(template.cadence, run_on)
    correlation_id = uuid.uuid4()

    invoice = Invoice(
        organization_id=template.organization_id,
        entity_id=template.entity_id,
        correlation_id=correlation_id,
        invoice_number=_generated_invoice_number(period_key),
        vendor_name=template.vendor_name,
        vendor_id=template.vendor_id,
        description=template.description,
        amount=template.amount,
        currency=template.currency,
        invoice_date=run_on,
        payment_terms=template.payment_terms,
        po_number=template.po_number,
        gl_account=template.gl_account,
        cost_center=template.cost_center,
        department=template.department,
        project=template.project,
        # Already coded — straight into the approval queue, never extraction.
        status=InvoiceStatus.ready_for_review,
        recurring_template_id=template.id,
        recurring_period_key=period_key,
    )
    try:
        # Savepoint so a unique-violation rolls back ONLY this generation — the
        # surrounding sweep keeps every sibling template it already generated.
        # `db.add` MUST be INSIDE the block: `SessionTransaction._take_snapshot`
        # flushes the session when a `begin_nested()` boundary opens, so a row
        # added first is INSERTed before the SAVEPOINT exists — the
        # IntegrityError then escapes the block and leaves the transaction
        # needing a rollback, so the recovery SELECT below raises
        # PendingRollbackError and takes the whole sweep tick down with it.
        # Same trap as `card_issuance.persist_card`; see that docstring.
        async with db.begin_nested():
            db.add(invoice)
            await db.flush()
    except IntegrityError:
        # The (template, period) slot was already claimed (concurrent sweep or a
        # manual generate-now). Return the existing invoice — no duplicate.
        existing = (
            await db.execute(
                select(Invoice).where(
                    Invoice.recurring_template_id == template.id,
                    Invoice.recurring_period_key == period_key,
                )
            )
        ).scalar_one_or_none()
        return existing

    # Snapshot the active workflow definition onto the invoice (frozen routing).
    await create_workflow_instance(db, invoice)

    await dispatch_audit(
        db,
        correlation_id=correlation_id,
        organization_id=template.organization_id,
        actor_id=actor_id,
        action="invoice.created",
        entity_type="invoice",
        entity_id=invoice.id,
        details={
            "source": "recurring_template",
            "recurring_template_id": str(template.id),
            "period_key": period_key,
            "status": InvoiceStatus.ready_for_review.value,
        },
    )
    await dispatch_audit(
        db,
        correlation_id=correlation_id,
        organization_id=template.organization_id,
        actor_id=actor_id,
        action="recurring_template.generated",
        entity_type="recurring_invoice_template",
        entity_id=template.id,
        details={"invoice_id": str(invoice.id), "period_key": period_key},
    )

    # Advance the template's scheduling cursor to the NEXT period — strictly
    # one whole period after this run_on, so the next tick doesn't re-pick it.
    template.generated_count = (template.generated_count or 0) + 1
    template.last_period_key = period_key
    template.last_generated_at = datetime.now(UTC)
    next_anchor = _add_months(run_on, _months_per_period(template.cadence))
    template.next_run_on = compute_next_run_on(
        template.cadence,
        template.day_of_period,
        after=next_anchor,
        start_date=template.start_date,
        end_date=template.end_date,
    )

    return invoice


# --------------------------------------------------------------------------- #
# Variance — flag an ARRIVED invoice that drifts from its template
# --------------------------------------------------------------------------- #


def flag_template_variance(
    invoice: Invoice,
    template: RecurringInvoiceTemplate,
    *,
    default_tolerance_pct: Decimal = DEFAULT_VARIANCE_TOLERANCE_PCT,
) -> dict | None:
    """Compare an arrived invoice's amount to its template's expected amount.

    Returns a warning dict when ``|delta%| >= tolerance`` (the template's
    ``variance_tolerance_pct`` override, else ``default_tolerance_pct``), else
    ``None``. Pure + Decimal — mirrors ``vendor_enrichment.detect_price_variance``
    tolerance approach. No PII in the message (amounts only).
    """
    if invoice.amount is None or template.amount is None or template.amount == 0:
        return None
    tolerance = (
        template.variance_tolerance_pct
        if template.variance_tolerance_pct is not None
        else default_tolerance_pct
    )
    expected = Decimal(str(template.amount))
    actual = Decimal(str(invoice.amount))
    delta = actual - expected
    delta_pct = (delta / expected * _HUNDRED).quantize(Decimal("0.1"))
    if abs(delta_pct) < tolerance:
        return None
    direction = "over" if delta > 0 else "under"
    return {
        "type": "recurring_variance",
        "severity": "warning",
        "message": (
            f"Amount {actual} is {delta_pct:+}% vs the recurring template "
            f"'{template.name}' expected amount {expected} ({direction} by tolerance)"
        ),
    }


# --------------------------------------------------------------------------- #
# Background sweep — mirrors discount_auto_trigger exactly
# --------------------------------------------------------------------------- #


@dataclass
class SweepResult:
    """Per-sweep outcome for logging + tests."""

    tenants_scanned: int = 0
    invoices_generated: int = 0
    failures: int = 0


async def generate_recurring_invoices_once(*, today: date | None = None) -> SweepResult:
    """One generation sweep across every tenant. Safe to call directly."""
    result = SweepResult()
    ref_today = today or datetime.now(UTC).date()

    async with control_session_factory() as ctrl:
        rows = await ctrl.execute(select(Organization.id, Organization.db_name))
        tenants = list(rows.all())

    for _org_id, db_name in tenants:
        result.tenants_scanned += 1
        try:
            result.invoices_generated += await _sweep_tenant(db_name, ref_today)
        except Exception as exc:  # noqa: BLE001 — one tenant must not halt the sweep
            logger.warning("[recurring] failed sweeping %s: %s", db_name, exc.__class__.__name__)
            result.failures += 1

    if result.invoices_generated or result.failures:
        logger.info(
            "[recurring] swept %d tenant(s); generated=%d failed_sweeps=%d",
            result.tenants_scanned,
            result.invoices_generated,
            result.failures,
        )
    return result


async def _sweep_tenant(db_name: str, today: date) -> int:
    """Generate due invoices for one tenant. Returns the count generated.

    Finds ``active`` templates with ``next_run_on <= today`` and generates the
    due period for each, capped at ``recurring_invoices_max_per_sweep`` per
    tick. Never moves money.
    """
    cap = int(settings.recurring_invoices_max_per_sweep)
    engine = create_async_engine(_make_tenant_url(db_name))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            templates = (
                (
                    await db.execute(
                        select(RecurringInvoiceTemplate)
                        .where(
                            RecurringInvoiceTemplate.status == STATUS_ACTIVE,
                            RecurringInvoiceTemplate.next_run_on.isnot(None),
                            RecurringInvoiceTemplate.next_run_on <= today,
                        )
                        .order_by(RecurringInvoiceTemplate.next_run_on)
                        .limit(cap)
                    )
                )
                .scalars()
                .all()
            )

            generated = 0
            for template in templates:
                run_on = template.next_run_on
                if run_on is None:
                    continue
                invoice = await generate_one(db, template, run_on=run_on, actor_id=None)
                # Only count a genuinely new invoice (idempotent no-op returns
                # the pre-existing row but the cursor still needs to advance so
                # the next tick doesn't re-pick the same template forever).
                if invoice is not None:
                    generated += 1
                # Defensive: if the cursor didn't advance (e.g. idempotent
                # no-op for an already-generated period), force it forward one
                # period so a stuck `next_run_on <= today` can't loop the sweep.
                if template.next_run_on is not None and template.next_run_on <= run_on:
                    months = _months_per_period(template.cadence)
                    template.next_run_on = compute_next_run_on(
                        template.cadence,
                        template.day_of_period,
                        after=_add_months(run_on, months),
                        start_date=template.start_date,
                        end_date=template.end_date,
                    )

            await db.commit()
            return generated
    finally:
        await engine.dispose()


async def run_recurring_invoices_loop() -> None:
    """Long-lived loop started in ``main.lifespan``; cancelled on shutdown."""
    interval = settings.recurring_invoices_interval_seconds
    logger.info("[recurring] started; interval=%ds", interval)
    try:
        while True:
            try:
                await generate_recurring_invoices_once()
            except Exception as exc:  # noqa: BLE001
                logger.error("[recurring] sweep raised: %s", exc.__class__.__name__)
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        logger.info("[recurring] shutting down")
        raise
