"""Deliver a webhook: sign, POST, classify, retry with backoff, dead-letter.

A delivery is attempted up to ``MAX_ATTEMPTS`` times. Each failed attempt
schedules the next via exponential backoff (``BACKOFF_BASE_SECONDS * 2**(n-1)``);
once attempts are exhausted the row moves to the ``dead`` state (dead-letter) and
is never retried automatically — only an explicit redelivery (the management API)
re-queues it.

Local-first: delivery is a plain ``httpx`` POST in-process. No cloud queue is
required. The immediate attempt fires from ``dispatch.emit_event`` on the running
loop; ``run_webhook_delivery_loop`` is the durable retry backstop, gated behind
``FEOH_WEBHOOKS_ENABLED`` (OFF by default in local dev).

PII discipline: the payloads themselves carry only invoice metadata (no bank /
tax / PAN fields — built in ``dispatch.py``), and the logs here record the
delivery id + status code + event type only, never the target URL's query string
or the response body.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.webhook import (
    DELIVERY_DEAD,
    DELIVERY_DELIVERED,
    DELIVERY_FAILED,
    DELIVERY_PENDING,
    WebhookDelivery,
    WebhookSubscription,
)

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
BACKOFF_BASE_SECONDS = 30
REQUEST_TIMEOUT_SECONDS = 10.0


def _next_backoff(attempt_count: int) -> timedelta:
    """Exponential backoff after ``attempt_count`` failed attempts."""
    return timedelta(seconds=BACKOFF_BASE_SECONDS * (2 ** max(0, attempt_count - 1)))


async def _post(target_url: str, body: bytes, signature: str, delivery: WebhookDelivery) -> int:
    """POST the signed body. Returns the HTTP status code; raises on transport
    error (timeout / connection refused) so the caller classifies it as a no-code
    failure."""
    headers = {
        "Content-Type": "application/json",
        "X-Webhook-Signature": signature,
        "X-Webhook-Event-Id": delivery.event_id,
        "X-Webhook-Event-Type": delivery.event_type,
    }
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        resp = await client.post(target_url, content=body, headers=headers)
        return resp.status_code


async def process_delivery(db: AsyncSession, delivery: WebhookDelivery) -> WebhookDelivery:
    """Attempt one delivery and persist the outcome. Commits the row.

    Terminal rows (delivered / dead) are returned untouched — safe to call on
    any row, so the sweep and the immediate path share one code path.
    """
    if delivery.status in (DELIVERY_DELIVERED, DELIVERY_DEAD):
        return delivery

    sub = (
        await db.execute(
            select(WebhookSubscription).where(WebhookSubscription.id == delivery.subscription_id)
        )
    ).scalar_one_or_none()
    if sub is None or not sub.active:
        # Subscription gone or deactivated since enqueue — dead-letter it; we
        # have no secret/target to deliver with.
        delivery.status = DELIVERY_DEAD
        delivery.next_attempt_at = None
        await db.commit()
        return delivery

    # Sign byte-identical payload bytes (frozen at emit time → stable signature).
    from app.services.webhooks.signing import sign_payload

    body = json.dumps(delivery.payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    signature = sign_payload(sub.signing_secret, body)

    from app.services.webhooks.url_guard import (
        WebhookTargetNotAllowed,
        ensure_public_webhook_target,
    )

    delivery.attempt_count += 1
    delivery.last_attempt_at = datetime.now(UTC)
    code: int | None = None
    ok = False
    try:
        # SSRF guard, re-checked at send time: the stored host is RE-resolved
        # so a DNS record that flipped to a private/loopback/metadata address
        # after create (TOCTOU / DNS rebinding) is refused, not POSTed to.
        await ensure_public_webhook_target(sub.target_url)
        code = await _post(sub.target_url, body, signature, delivery)
        ok = 200 <= code < 300
    except WebhookTargetNotAllowed:
        # PII-free by design: delivery id + event type only — never the URL,
        # host, or the address it resolved to.
        logger.warning(
            "webhook delivery refused: target not publicly routable: delivery=%s event=%s",
            delivery.id,
            delivery.event_type,
        )
    except Exception as exc:  # noqa: BLE001 — any transport error is a failed attempt
        logger.warning(
            "webhook delivery transport error: delivery=%s event=%s err=%s",
            delivery.id,
            delivery.event_type,
            type(exc).__name__,
        )

    delivery.response_code = code
    if ok:
        delivery.status = DELIVERY_DELIVERED
        delivery.next_attempt_at = None
    elif delivery.attempt_count >= MAX_ATTEMPTS:
        delivery.status = DELIVERY_DEAD
        delivery.next_attempt_at = None
        logger.warning(
            "webhook delivery dead-lettered: delivery=%s event=%s attempts=%s code=%s",
            delivery.id,
            delivery.event_type,
            delivery.attempt_count,
            code,
        )
    else:
        delivery.status = DELIVERY_FAILED
        delivery.next_attempt_at = datetime.now(UTC) + _next_backoff(delivery.attempt_count)

    await db.commit()
    return delivery


async def process_delivery_by_id(delivery_id: uuid.UUID) -> None:
    """Load a delivery by id (own control-plane session) and process it once.

    Used by the fire-and-forget immediate attempt. Best-effort: any failure is
    logged, never raised (the outcome lives on the row).
    """
    from app.database import control_session_factory

    try:
        async with control_session_factory() as db:
            delivery = (
                await db.execute(select(WebhookDelivery).where(WebhookDelivery.id == delivery_id))
            ).scalar_one_or_none()
            if delivery is not None:
                await process_delivery(db, delivery)
    except Exception:  # noqa: BLE001
        logger.exception("webhook immediate delivery failed: delivery=%s", delivery_id)


async def deliver_due(db: AsyncSession, *, limit: int = 100) -> int:
    """Process every delivery whose retry is due. Returns the count attempted."""
    now = datetime.now(UTC)
    rows = (
        (
            await db.execute(
                select(WebhookDelivery)
                .where(
                    WebhookDelivery.status.in_((DELIVERY_PENDING, DELIVERY_FAILED)),
                    WebhookDelivery.next_attempt_at.isnot(None),
                    WebhookDelivery.next_attempt_at <= now,
                )
                .order_by(WebhookDelivery.next_attempt_at.asc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    for delivery in rows:
        await process_delivery(db, delivery)
    return len(rows)


async def run_webhook_delivery_loop() -> None:
    """Background retry sweep — re-attempts due failed/pending deliveries.

    Gated behind ``FEOH_WEBHOOKS_ENABLED`` at the call site (main.lifespan), like
    every other background sweep. The immediate emit attempt handles the happy
    path; this loop is the durable backstop for retries + anything queued while
    no event loop was available.
    """
    from app.database import control_session_factory

    logger.info("[webhooks] delivery sweep started")
    while True:
        try:
            async with control_session_factory() as db:
                await deliver_due(db)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — a sweep error must not kill the loop
            logger.exception("[webhooks] delivery sweep tick failed")
        await asyncio.sleep(settings.webhooks_delivery_interval_seconds)
