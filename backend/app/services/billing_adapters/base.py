"""Base billing-provider adapter interface + shared types.

A billing adapter wraps an external billing/subscription provider (Stripe
Billing, Chargebee, …). It is the seam between our control-plane ``Plan`` /
``Subscription`` model and the provider's hosted objects. This FIRST SLICE
defines the interface + a deterministic local-first ``mock`` (the default) and a
fail-closed ``stripe_billing`` skeleton with the correct wire shape.

The adapter NEVER moves money on its own and NEVER persists our models — it
talks to the provider and returns normalized DTOs; the caller persists.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass(frozen=True)
class ProviderSubscription:
    """Normalized view of a provider-side subscription."""

    external_subscription_id: str
    status: str  # trialing | active | past_due | canceled (provider-mapped)
    plan_code: str


@dataclass(frozen=True)
class CreateSubscriptionRequest:
    organization_id: str
    plan_code: str
    # Exact money — the plan's monthly price, passed through for the provider to
    # price the line item. Decimal, never float.
    monthly_price: Decimal
    currency: str = "USD"
    trial_days: int = 0
    # Stable idempotency key so a retried create never double-subscribes the org
    # at the provider (mirrors the payment-adapter idempotency discipline).
    idempotency_key: str = ""


@dataclass(frozen=True)
class UsageReport:
    organization_id: str
    period: str  # YYYY-MM
    # meter name -> quantity/amount as an exact decimal string (no float).
    meters: dict[str, str] = field(default_factory=dict)
    external_subscription_id: str | None = None


@dataclass(frozen=True)
class BillingWebhookEvent:
    """A verified, deduped provider webhook event."""

    event_id: str
    event_type: str  # e.g. "subscription.updated", "invoice.payment_failed"
    external_subscription_id: str | None
    # Mapped lifecycle status when the event implies one, else None.
    status: str | None = None


class BillingAdapter:
    """Base class for billing providers."""

    provider_name: str = "base"

    def __init__(self, config: dict | None = None):
        self.config = config or {}

    async def create_subscription(
        self, request: CreateSubscriptionRequest
    ) -> ProviderSubscription:
        raise NotImplementedError

    async def get_subscription(self, external_subscription_id: str) -> ProviderSubscription:
        raise NotImplementedError

    async def report_usage(self, report: UsageReport) -> None:
        raise NotImplementedError

    def parse_webhook(self, headers: dict, body: bytes) -> BillingWebhookEvent | None:
        """Verify the provider HMAC over ``body`` and return a normalized event.

        Returns ``None`` on any verification/parse failure so the webhook route
        can ack 204 silently (no enumeration). Dedupe-by-event-id is the route's
        responsibility via ``services/webhook_security.is_event_already_processed``.
        """
        raise NotImplementedError

    async def test_connection(self) -> bool:
        raise NotImplementedError
