"""Emit a domain event to every matching active subscription.

``emit_event`` is the single chokepoint the three event sources call. It is
**best-effort and never raises into its caller** — a webhook failure must never
break the invoice/payment transition that produced the event (same contract as
``notification_dispatch.notify_event``). It:

  1. opens its OWN short-lived control-plane session (the caller's session is
     tenant-scoped; subscriptions live in the control plane — mirrors
     ``audit_dispatch.dispatch_auth_audit``),
  2. finds every active subscription for the org subscribed to the event type,
  3. inserts one ``WebhookDelivery`` (status=pending) per subscription, deduped
     on ``(subscription_id, event_id)`` so a re-fired/replayed event can't queue
     the same delivery twice (webhook discipline — dedupe by event id), and
  4. kicks off an in-process, fire-and-forget delivery attempt for each new row
     (local-first: deliveries run in-process, no cloud queue). The background
     retry sweep (``delivery.run_webhook_delivery_loop``) is the durable backstop
     for anything the immediate attempt doesn't land.

The payload is built here, ONCE, and frozen on the delivery row so a retry
re-sends byte-identical bytes (and thus the same signature).
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.models.webhook import (
    DELIVERY_PENDING,
    EVENT_EXCEPTION_RAISED,
    EVENT_INVOICE_APPROVED,
    EVENT_PAYMENT_SETTLED,
    WebhookDelivery,
    WebhookSubscription,
)

logger = logging.getLogger(__name__)


def _money_str(value) -> str | None:
    """Money serialises as an exact string (never float) — money-is-exact."""
    if value is None:
        return None
    return str(value)


async def emit_event(
    *,
    organization_id: uuid.UUID,
    event_type: str,
    event_key: str,
    data: dict,
) -> None:
    """Enqueue + best-effort deliver a webhook event. Never raises.

    ``event_key`` is a stable, caller-supplied identity for the event occurrence
    (e.g. the invoice id) — combined with ``event_type`` it forms the per-event
    id used for dedupe, so the same approval firing twice produces ONE delivery
    per subscription.
    """
    if not settings.webhooks_enabled:
        # Master kill switch OFF (local-dev default): emit is a silent no-op so
        # a fresh clone never makes outbound HTTP calls.
        return
    try:
        await _emit(
            organization_id=organization_id,
            event_type=event_type,
            event_key=event_key,
            data=data,
        )
    except Exception:  # noqa: BLE001 — emit must never break the caller's transition
        logger.exception("webhook emit failed for org=%s event=%s", organization_id, event_type)


async def _emit(
    *,
    organization_id: uuid.UUID,
    event_type: str,
    event_key: str,
    data: dict,
) -> None:
    from app.database import control_session_factory

    # event_id is deterministic per (type, occurrence) so a re-fire dedupes.
    event_id = f"{event_type}:{event_key}"
    payload = {
        "id": event_id,
        "type": event_type,
        "created_at": datetime.now(UTC).isoformat(),
        "organization_id": str(organization_id),
        "data": data,
    }

    new_delivery_ids: list[uuid.UUID] = []
    async with control_session_factory() as db:
        subs = (
            (
                await db.execute(
                    select(WebhookSubscription).where(
                        WebhookSubscription.organization_id == organization_id,
                        WebhookSubscription.active.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )
        for sub in subs:
            if event_type not in (sub.event_types or []):
                continue
            delivery = WebhookDelivery(
                id=uuid.uuid4(),
                subscription_id=sub.id,
                organization_id=organization_id,
                event_id=event_id,
                event_type=event_type,
                payload=payload,
                status=DELIVERY_PENDING,
                attempt_count=0,
                next_attempt_at=datetime.now(UTC),
            )
            db.add(delivery)
            try:
                await db.commit()
            except IntegrityError:
                # Duplicate (subscription, event_id) — this event already queued
                # for this subscription. Dedupe wins; skip silently.
                await db.rollback()
                continue
            new_delivery_ids.append(delivery.id)

    # Fire-and-forget an immediate attempt for each freshly-queued delivery so a
    # local dev / single-process deploy delivers without waiting for the sweep.
    for did in new_delivery_ids:
        _spawn_immediate_attempt(did)


def _spawn_immediate_attempt(delivery_id: uuid.UUID) -> None:
    """Best-effort immediate delivery on the running event loop, if any."""
    import asyncio

    from app.services.webhooks.delivery import process_delivery_by_id

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop (e.g. a sync worker thread) — the sweep will pick it up.
        return
    task = loop.create_task(process_delivery_by_id(delivery_id))
    # Swallow any task exception so an unawaited failure never surfaces as an
    # "exception was never retrieved" warning — delivery errors are persisted on
    # the row, not raised.
    task.add_done_callback(lambda t: t.exception())


# ---------------------------------------------------------------------------
# Typed emit helpers for the three event sources — keep the call sites at the
# emit sites a single, obvious line.
# ---------------------------------------------------------------------------


async def emit_invoice_approved(invoice) -> None:
    """Emit ``invoice.approved`` for a freshly-approved invoice."""
    await emit_event(
        organization_id=invoice.organization_id,
        event_type=EVENT_INVOICE_APPROVED,
        event_key=str(invoice.id),
        data={
            "invoice_id": str(invoice.id),
            "invoice_number": getattr(invoice, "invoice_number", None),
            "vendor_name": getattr(invoice, "vendor_name", None),
            "amount": _money_str(getattr(invoice, "amount", None)),
            "currency": getattr(invoice, "currency", None) or "USD",
            "status": getattr(getattr(invoice, "status", None), "value", None),
        },
    )


async def emit_payment_settled(invoice) -> None:
    """Emit ``payment.settled`` when an invoice reaches the ``paid`` state."""
    await emit_event(
        organization_id=invoice.organization_id,
        event_type=EVENT_PAYMENT_SETTLED,
        event_key=str(invoice.id),
        data={
            "invoice_id": str(invoice.id),
            "invoice_number": getattr(invoice, "invoice_number", None),
            "vendor_name": getattr(invoice, "vendor_name", None),
            "amount": _money_str(getattr(invoice, "amount", None)),
            "currency": getattr(invoice, "currency", None) or "USD",
            "status": getattr(getattr(invoice, "status", None), "value", None),
        },
    )


async def emit_exception_raised(
    *, organization_id, exception_id, invoice_id, exception_type
) -> None:
    """Emit ``exception.raised`` when an AP exception is opened.

    NOTE: not wired to a call site in this slice — see ``backend/docs/public-api.md``
    § Outbound webhooks (deferred event source). Kept here so the next slice
    only adds the one emit line at a non-conflicting exception chokepoint.
    """
    await emit_event(
        organization_id=organization_id,
        event_type=EVENT_EXCEPTION_RAISED,
        event_key=str(exception_id),
        data={
            "exception_id": str(exception_id),
            "invoice_id": str(invoice_id) if invoice_id else None,
            "exception_type": exception_type,
        },
    )
