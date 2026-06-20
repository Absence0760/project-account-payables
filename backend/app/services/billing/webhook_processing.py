"""Apply a verified, deduped billing webhook event to our control-plane state.

The webhook route (``api/billing_webhook.py``) verifies the provider HMAC,
dedupes by ``event_id``, and hands a normalized :class:`BillingWebhookEvent`
here. This module owns the *effect*: resolve the ``Subscription`` the event
refers to (by its live provider id), apply the lifecycle transition, and write a
PII-free audit row.

Design:
  * **Control-plane only** — ``Plan`` / ``Subscription`` live in the control DB
    keyed by org; this never touches a tenant DB for the mutation itself (the
    audit row is written into the tenant ``audit_log`` via the shared
    ``dispatch_auth_audit`` helper, which resolves the tenant DB from the org).
  * **Idempotent** — applying the same target status twice is a no-op (the
    route's dedupe is the first line of defense; this is the second). A status
    that doesn't change writes no audit row.
  * **Fail-soft + silent** — an event referencing an unknown subscription, or
    carrying no mapped status, is dropped with a PII-free reason code. The route
    204s on every outcome.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing import SUBSCRIPTION_STATUSES, Subscription
from app.services.audit_dispatch import dispatch_auth_audit
from app.services.billing_adapters.base import BillingWebhookEvent

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WebhookApplyResult:
    """Outcome of applying one billing webhook event."""

    applied: bool
    reason: str | None = None
    # The status the subscription ended up in (for logging/tests), if resolved.
    new_status: str | None = None


async def apply_billing_event(
    control_db: AsyncSession, *, event: BillingWebhookEvent
) -> WebhookApplyResult:
    """Drive a subscription lifecycle transition from a verified webhook event.

    Resolves the ``Subscription`` by ``external_subscription_id`` (the live
    provider's id, persisted on the row when the subscription was created at the
    provider). Applies ``event.status`` when it is one of our four lifecycle
    states and differs from the current status, writing an append-only
    ``billing.subscription_<status>`` audit row.
    """
    if not event.external_subscription_id:
        # Account-level events with no subscription reference (e.g. a customer
        # update) carry no lifecycle effect for us in this slice — drop quietly.
        return WebhookApplyResult(applied=False, reason="no_subscription_reference")

    if not event.status:
        # An event we don't map to a lifecycle status (e.g. an informational
        # invoice.paid that didn't move the subscription). Nothing to apply.
        return WebhookApplyResult(applied=False, reason="no_mapped_status")

    if event.status not in SUBSCRIPTION_STATUSES:
        # Defensive: the adapter maps statuses, but never trust an out-of-range
        # value into the DB column.
        return WebhookApplyResult(applied=False, reason="unknown_status")

    subscription = (
        await control_db.execute(
            select(Subscription).where(
                Subscription.external_subscription_id == event.external_subscription_id
            )
        )
    ).scalar_one_or_none()
    if subscription is None:
        # Unknown subscription — never enumerate; the route 204s regardless.
        return WebhookApplyResult(applied=False, reason="unknown_subscription")

    if subscription.status == event.status:
        # Idempotent no-op: the target state is already current. No audit row for
        # a non-change (mirrors transition_invoice's "no-op writes no row").
        return WebhookApplyResult(
            applied=False, reason="already_in_status", new_status=event.status
        )

    previous = subscription.status
    subscription.status = event.status

    # Append-only audit (SOX): subscription status is a regulated control-plane
    # mutation. PII-free — only the org, the lifecycle states, and the event id.
    # Dispatched BEFORE the control commit (mirrors the transition_invoice
    # chokepoint) so the status change is never durably persisted without an
    # audit attempt. dispatch_auth_audit opens its own short-lived tenant-DB
    # session and is fail-soft, so it can't break the transition.
    await dispatch_auth_audit(
        organization_id=subscription.organization_id,
        actor_id=None,  # provider-driven, no human actor
        action=f"billing.subscription_{event.status}",
        entity_id=subscription.id,
        details={
            "from_status": previous,
            "to_status": event.status,
            "event_id": event.event_id,
            "event_type": event.event_type,
        },
    )
    await control_db.commit()

    return WebhookApplyResult(applied=True, new_status=event.status)
