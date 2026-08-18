"""Dunning / past-due automation sweep — control-plane subscriptions.

The provider's own retry schedule (Stripe Smart Retries) normally drives a
failing subscription ``active → past_due → canceled`` and we learn about each
hop via the inbound billing webhook (``api/billing_webhook.py``). This sweep is
the **backstop** for when a terminal provider webhook never arrives: a
subscription that has sat ``past_due`` longer than the grace window
(``FEOH_BILLING_DUNNING_GRACE_DAYS``) is flagged ``canceled`` with an append-only
audit row.

Money-path boundary (important)
-------------------------------
This sweep ONLY changes a ``Subscription`` lifecycle status (``past_due`` →
``canceled``). It never charges a card, never refunds, never creates any
payment-side row — it can't move money. Down-grading entitlements is a
consequence read by ``get_entitlements`` (a canceled subscription grants
nothing), not a money operation.

Control-plane only
------------------
Unlike the tenant-fan-out sweeps (``contract_renewal`` / ``recurring_invoices``),
``Subscription`` lives in the control DB keyed by org, so this sweep runs one
query against the control plane — no per-tenant engine churn.

Idempotency
-----------
Only ``past_due`` rows are touched, and canceling one moves it out of
``past_due``; a re-run never re-cancels (the status guard is the dedupe).

Mirrors the other sweeps: long-lived asyncio task started in ``main.lifespan``,
one row's failure logged but never halts the sweep. Disabled by default
(``FEOH_BILLING_DUNNING_ENABLED``). See ``backend/docs/billing.md``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import control_session_factory
from app.models.billing import Subscription
from app.services.audit_dispatch import dispatch_auth_audit
from app.services.sweep_health import SWEEP_BILLING_DUNNING, run_sweep_loop

logger = logging.getLogger(__name__)


async def run_dunning_once(control_db: AsyncSession, *, now: datetime | None = None) -> int:
    """Cancel ``past_due`` subscriptions past the grace window. Returns the count.

    A ``past_due`` subscription whose current billing period ended more than
    ``grace_days`` ago is canceled. Each cancellation writes a PII-free
    ``billing.subscription_canceled`` audit row (dunning attribution, no human
    actor).

    Reads the **persisted** ``current_period_end``, deliberately NOT the
    rolled-forward window ``period.current_period`` resolves for the summary
    endpoint. The two answer different questions: the summary asks "which
    period is this subscription in" (always ending in the future), while
    dunning asks "how long has this gone unpaid", whose anchor is the last
    period boundary the subscription actually billed at. Resolving forward here
    would put the end date permanently in the future and the sweep could never
    cancel anything.

    Until ``plan_catalog.ensure_subscription`` started stamping the first
    window, nothing wrote this column at all, so the "no period end recorded ⇒
    overdue by default" branch below was the ONLY case: every ``past_due``
    subscription was canceled on its first tick and
    ``FEOH_BILLING_DUNNING_GRACE_DAYS`` could not apply to any subscription
    that can exist. It remains the branch a pre-existing row takes.
    """
    now = now or datetime.now(UTC)
    grace = timedelta(days=settings.billing_dunning_grace_days)
    cutoff = now - grace

    rows = (
        (await control_db.execute(select(Subscription).where(Subscription.status == "past_due")))
        .scalars()
        .all()
    )

    canceled = 0
    for sub in rows:
        period_end = sub.current_period_end
        # A period end in the future means the grace clock hasn't started; only
        # cancel when overdue past the window (or when no period end is known —
        # a stuck row with nothing to anchor the clock to is overdue by default).
        if period_end is not None and period_end > cutoff:
            continue
        previous = sub.status
        sub.status = "canceled"
        # Audit BEFORE the control commit (mirrors transition_invoice) so the
        # cancellation is never durably persisted without an audit attempt.
        # dispatch_auth_audit opens its own tenant-DB session and is fail-soft.
        await dispatch_auth_audit(
            organization_id=sub.organization_id,
            actor_id=None,  # dunning automation, no human actor
            action="billing.subscription_canceled",
            entity_id=sub.id,
            details={
                "from_status": previous,
                "to_status": "canceled",
                "reason": "dunning_grace_expired",
                "grace_days": settings.billing_dunning_grace_days,
            },
        )
        await control_db.commit()
        canceled += 1

    return canceled


async def _dunning_tick() -> int:
    """One sweep tick. Returns the cancellation count so the shared runner can
    record it (``extract_counts`` maps a bare int to ``{"count": n}``)."""
    async with control_session_factory() as control_db:
        count = await run_dunning_once(control_db)
    if count:
        logger.info("billing dunning sweep canceled %d past-due subscription(s)", count)
    return count


async def run_dunning_loop() -> None:
    """Long-lived sweep loop. Started in ``main.lifespan`` when enabled.

    Body is the shared ``sweep_health.run_sweep_loop``, which also replaces the
    old ``logger.exception`` on a failed tick: that call attaches the full
    traceback (including ``str(exc)``) to the record, and a control-plane /
    billing-adapter error can carry a customer identifier. The class name alone
    is what reaches the sink now, consistent with every other sweep.
    """
    await run_sweep_loop(
        SWEEP_BILLING_DUNNING,
        _dunning_tick,
        interval_seconds=settings.billing_dunning_interval_seconds,
        log=logger,
        log_prefix="[billing-dunning]",
    )
