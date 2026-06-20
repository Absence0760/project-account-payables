"""Stripe Billing adapter — SKELETON (later slice wires the live API calls).

Fail-closed posture: every method that would touch Stripe raises
``BillingNotConfigured`` when no API key is present, so selecting this provider
without a key can never silently no-op or fall back to a permissive path. The
real secret arrives via sops (``AP_BILLING_STRIPE_API_KEY`` /
``AP_BILLING_STRIPE_WEBHOOK_SECRET``) — there is NO hardcoded fallback.

The wire shape is correct (the DTOs the dispatcher/caller expect, the webhook
HMAC verification + the dedupe contract), but the actual ``stripe`` SDK / HTTP
calls are intentionally left as documented skeletons for the next slice (see the
``TODO(jared)`` markers). ``parse_webhook`` IS implemented end-to-end (HMAC
verify via the shared ``webhook_security`` helper) because the webhook invariant
requires it now.
"""

from __future__ import annotations

import json
import logging

from app.services.billing_adapters.base import (
    BillingAdapter,
    BillingWebhookEvent,
    CreateSubscriptionRequest,
    ProviderSubscription,
    UsageReport,
)
from app.services.billing_adapters.dispatcher import register_billing_adapter
from app.services.webhook_security import extract_signature_header, verify_hmac_sha256

logger = logging.getLogger(__name__)


class BillingNotConfigured(RuntimeError):
    """Raised when the Stripe adapter is selected without a configured key."""


# Map Stripe subscription statuses → our four-state lifecycle. ``incomplete`` /
# ``unpaid`` collapse to ``past_due`` (dunning is a later slice).
_STATUS_MAP = {
    "trialing": "trialing",
    "active": "active",
    "past_due": "past_due",
    "unpaid": "past_due",
    "incomplete": "past_due",
    "incomplete_expired": "canceled",
    "canceled": "canceled",
}


@register_billing_adapter("stripe_billing")
class StripeBillingAdapter(BillingAdapter):
    provider_name = "stripe_billing"

    @property
    def _api_key(self) -> str:
        return (self.config or {}).get("stripe_api_key") or ""

    @property
    def _webhook_secret(self) -> str:
        return (self.config or {}).get("stripe_webhook_secret") or ""

    def _require_key(self) -> None:
        if not self._api_key:
            # Fail closed — never proceed against Stripe without a key.
            raise BillingNotConfigured("Stripe billing is not configured (no API key)")

    async def create_subscription(
        self, request: CreateSubscriptionRequest
    ) -> ProviderSubscription:
        self._require_key()
        # TODO(jared): later slice — stripe.Subscription.create(...) with the
        # org's customer id, the plan's price id, trial_period_days, and the
        # idempotency_key header. Map the returned status via _STATUS_MAP.
        raise NotImplementedError("Stripe create_subscription is a later slice")

    async def get_subscription(self, external_subscription_id: str) -> ProviderSubscription:
        self._require_key()
        # TODO(jared): later slice — stripe.Subscription.retrieve(external_subscription_id).
        raise NotImplementedError("Stripe get_subscription is a later slice")

    async def report_usage(self, report: UsageReport) -> None:
        self._require_key()
        # TODO(jared): later slice — stripe.billing.MeterEvent / usage records per meter.
        raise NotImplementedError("Stripe report_usage is a later slice")

    def parse_webhook(self, headers: dict, body: bytes) -> BillingWebhookEvent | None:
        """Verify the Stripe-Signature HMAC over the raw body and normalize.

        Fail-closed: a missing secret/signature or a bad HMAC returns ``None``
        (route 204s silently). Dedupe-by-event-id is the route's job via
        ``webhook_security.is_event_already_processed``.
        """
        signature = extract_signature_header(headers, "Stripe-Signature", "stripe-signature")
        if not verify_hmac_sha256(self._webhook_secret, body, signature):
            return None
        try:
            payload = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
        event_id = payload.get("id")
        event_type = payload.get("type")
        if not event_id or not event_type:
            return None
        obj = (payload.get("data") or {}).get("object") or {}
        raw_status = obj.get("status")
        return BillingWebhookEvent(
            event_id=str(event_id),
            event_type=str(event_type),
            external_subscription_id=obj.get("id"),
            status=_STATUS_MAP.get(raw_status) if raw_status else None,
        )

    async def test_connection(self) -> bool:
        # Fail closed without a key rather than reporting healthy.
        return bool(self._api_key)
