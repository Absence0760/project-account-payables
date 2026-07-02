"""Payment-status reconciliation sweeper.

When a payment lands at a real processor we expect the webhook to drive
the status to its terminal state (`completed` / `failed`). Webhooks
get lost — endpoint flapping, spurious 5xx, signature drift after a
secret rotation. Without backstop polling, a stuck `submitted` row is
indistinguishable from one that's still legitimately in flight.

This module sweeps every tenant DB on a timer and re-polls each
non-terminal payment's status via the configured adapter. Status is
written back the same way the webhook handler does it. Old enough
non-terminal rows get marked `failed` to clear the queue (operators can
still investigate via the audit log).

Modeled after services/extraction_reaper.py and
services/approval_escalation.py — same async-loop shape.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.database import _make_tenant_url, control_session_factory
from app.models.organization import Organization
from app.models.payment import Payment
from app.services.payment_adapters import PaymentStatus, get_payment_adapter

logger = logging.getLogger(__name__)


@dataclass
class ReconcileResult:
    tenants_scanned: int = 0
    payments_polled: int = 0
    payments_resolved: int = 0  # transitioned to a terminal status
    payments_aged_out: int = 0  # forced `failed` after the max age
    failures: int = 0  # tenants we couldn't reach


async def _audit_reconcile_transition(
    db,
    *,
    org: Organization,
    payment: Payment,
    previous_status: str | None,
    source: str,
) -> None:
    """Append-only audit row for a reconciler-driven terminal transition.

    The backstop sweep flips an in-flight payment to its terminal status and
    stamps the regulated ``completed_at`` exactly like the webhook path does;
    per the project invariant that money-status change must produce an audit
    row. Actor is None (system-initiated by the sweep, not a user). PII-free:
    only ids, status, the Decimal amount as a string, and the reference.
    """
    import uuid as _uuid

    from app.services.audit_dispatch import dispatch_audit

    await dispatch_audit(
        db,
        correlation_id=payment.correlation_id or _uuid.uuid4(),
        organization_id=org.id,
        actor_id=None,
        action=f"payment.{payment.status}",
        entity_type="payment",
        entity_id=payment.id,
        details={
            "status": payment.status,
            "previous_status": previous_status or "unknown",
            "method": payment.method,
            "amount": str(payment.amount),
            "reference": payment.reference,
            "source": source,
            "payment_run_id": str(payment.payment_run_id) if payment.payment_run_id else None,
        },
    )


async def reconcile_once(*, now: datetime | None = None) -> ReconcileResult:
    """One sweep across every tenant. Safe for direct CLI invocation."""
    now = now or datetime.now(UTC)
    result = ReconcileResult()

    async with control_session_factory() as ctrl:
        rows = await ctrl.execute(select(Organization))
        tenants = list(rows.scalars().all())

    for org in tenants:
        result.tenants_scanned += 1
        try:
            outcome = await _reconcile_tenant(org, now)
            result.payments_polled += outcome["polled"]
            result.payments_resolved += outcome["resolved"]
            result.payments_aged_out += outcome["aged_out"]
        except Exception as exc:  # noqa: BLE001
            # Log the exception class, not the message — a processor
            # SDK could surface partial PAN / account numbers in its
            # error string (invariant #7).
            logger.warning(
                "[payment-reconciler] failed to sweep %s: %s",
                org.db_name,
                exc.__class__.__name__,
            )
            result.failures += 1

    if result.payments_polled or result.failures:
        logger.info(
            "[payment-reconciler] swept %d tenant(s); polled=%d resolved=%d "
            "aged_out=%d failures=%d",
            result.tenants_scanned,
            result.payments_polled,
            result.payments_resolved,
            result.payments_aged_out,
            result.failures,
        )
    return result


async def _reconcile_tenant(org: Organization, now: datetime) -> dict[str, int]:
    """Re-poll every non-terminal payment in one tenant DB.

    The cutoff for re-polling: `submitted_at` older than
    `AP_PAYMENT_RECONCILE_AFTER_MINUTES`. Polling earlier is wasteful;
    polling never can hide a stuck row indefinitely. Anything older
    than `AP_PAYMENT_RECONCILE_MAX_AGE_HOURS` flips to `failed` with a
    diagnostic reason — the operator can pull the row up by audit log
    and chase the rail manually.
    """
    settle_after = timedelta(minutes=settings.payment_reconcile_after_minutes)
    max_age = timedelta(hours=settings.payment_reconcile_max_age_hours)

    payment_config = (org.settings or {}).get("payments") or {}
    if not payment_config.get("provider"):
        # Org hasn't configured a processor; nothing to poll.
        return {"polled": 0, "resolved": 0, "aged_out": 0}

    adapter = get_payment_adapter(payment_config)

    engine = create_async_engine(_make_tenant_url(org.db_name))
    factory = async_sessionmaker(engine, expire_on_commit=False)

    polled = 0
    resolved = 0
    aged_out = 0
    # Runs holding a payment the sweep just settled to `completed`. After the
    # commit we hand these to dispatch_payment_sync — the exact downstream the
    # webhook path fires — so the invoice flips payment_scheduled → paid and the
    # ERP is notified. Without this the reconciler settled the payment row but
    # left the invoice stuck in payment_scheduled forever (the missed-webhook
    # case is precisely what the reconciler exists to handle).
    runs_to_sync: set = set()

    try:
        async with factory() as db:
            stuck = (
                (
                    await db.execute(
                        select(Payment).where(
                            Payment.status.in_(["submitted", "processing"]),
                            Payment.submitted_at.isnot(None),
                        )
                    )
                )
                .scalars()
                .all()
            )

            for payment in stuck:
                age = now - (payment.submitted_at or now)
                if age < settle_after:
                    continue
                # Past the absolute max age — give up and mark failed.
                if age > max_age:
                    # Lock + re-read the committed state before clobbering it:
                    # a webhook may have settled this payment between the bulk
                    # read above and now. Without the lock + re-check the
                    # reconciler would overwrite the webhook's terminal status
                    # (and its regulated completed_at) and write a duplicate
                    # transition audit row.
                    await db.refresh(payment, with_for_update=True)
                    if payment.status not in ("submitted", "processing"):
                        continue
                    previous_status = payment.status
                    payment.status = "failed"
                    payment.failure_reason = (
                        f"reconciler_max_age_exceeded after {age.total_seconds() / 3600:.1f}h"
                    )
                    payment.completed_at = now
                    aged_out += 1
                    await _audit_reconcile_transition(
                        db,
                        org=org,
                        payment=payment,
                        previous_status=previous_status,
                        source="reconciler_aged_out",
                    )
                    continue

                if not payment.provider_payment_id:
                    # Submitted with no processor id — the executor
                    # logged that as a failure already; just advance.
                    continue

                polled += 1
                try:
                    upstream = await adapter.get_payment_status(payment.provider_payment_id)
                except Exception as exc:  # noqa: BLE001
                    # See note above — log the class, not the message.
                    logger.info(
                        "[payment-reconciler] adapter %s raised on %s: %s",
                        adapter.provider_name,
                        payment.id,
                        exc.__class__.__name__,
                    )
                    continue

                if upstream == payment.status:
                    continue
                # Webhooks could have raced us — only accept terminal
                # status updates from the poll. The async webhook path
                # handles the in-flight transitions.
                if upstream in (
                    PaymentStatus.completed,
                    PaymentStatus.failed,
                    PaymentStatus.cancelled,
                ):
                    # Lock + re-read before writing the terminal status (see the
                    # max-age branch). A webhook that raced us between the bulk
                    # read and this poll already settled the row; skip rather
                    # than overwrite its completed_at + double-audit.
                    await db.refresh(payment, with_for_update=True)
                    if payment.status not in ("submitted", "processing"):
                        continue
                    previous_status = payment.status
                    payment.status = upstream.value
                    payment.completed_at = now
                    resolved += 1
                    if payment.status == "completed" and payment.payment_run_id:
                        runs_to_sync.add(payment.payment_run_id)
                    await _audit_reconcile_transition(
                        db,
                        org=org,
                        payment=payment,
                        previous_status=previous_status,
                        source="reconciler_poll",
                    )

            if polled or aged_out:
                await db.commit()
    finally:
        await engine.dispose()

    # Mirror the webhook's downstream: flip each completed payment's invoice to
    # `paid` and notify the ERP. Runs after the commit so the sync sees the
    # settled status. Best-effort — a sync failure must not abort the sweep.
    if runs_to_sync:
        from app.services.payment_erp_sync import dispatch_payment_sync

        for run_id in runs_to_sync:
            try:
                await dispatch_payment_sync(run_id, org.id)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[payment-reconciler] payment-sync dispatch failed for run %s: %s",
                    run_id,
                    exc.__class__.__name__,
                )

    return {"polled": polled, "resolved": resolved, "aged_out": aged_out}


async def run_reconciler_loop() -> None:
    """Long-lived loop. Started in `main.lifespan` on app startup,
    cancelled on shutdown."""
    interval = settings.payment_reconcile_interval_seconds
    logger.info("[payment-reconciler] started; interval=%ds", interval)
    try:
        while True:
            try:
                await reconcile_once()
            except Exception as exc:  # noqa: BLE001
                # Log the class, not the message — see the note on the inner
                # per-payment catch above (PII-out-of-logs invariant).
                logger.error(
                    "[payment-reconciler] sweep raised: %s",
                    exc.__class__.__name__,
                    exc_info=True,
                )
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        logger.info("[payment-reconciler] shutting down")
        raise
