"""Mock billing adapter — the local-first DEFAULT.

Deterministic, in-process, no network and no credential. ``pnpm dev`` and the
test suite run entirely against this: creating a subscription returns a stable
synthetic ``external_subscription_id`` derived from the org id, usage reports are
accepted and dropped (nothing to bill locally), and ``parse_webhook`` understands
a simple dev JSON envelope so the webhook route can be exercised without Stripe.

It never moves money — there is no money to move locally.
"""

from __future__ import annotations

import json
from decimal import Decimal

from app.services.billing_adapters.base import (
    BillingAdapter,
    BillingWebhookEvent,
    CreateSubscriptionRequest,
    ProviderSubscription,
    UsageReport,
)
from app.services.billing_adapters.dispatcher import register_billing_adapter


@register_billing_adapter("mock")
class MockBillingAdapter(BillingAdapter):
    provider_name = "mock"

    async def ensure_customer(
        self, *, organization_id: str, name: str | None = None, email: str | None = None
    ) -> str:
        # Deterministic synthetic id derived from the org — stable across retries.
        return f"mock_cus_{organization_id}"

    async def ensure_price(
        self, *, plan_code: str, monthly_price: Decimal, currency: str = "USD"
    ) -> str:
        # Deterministic synthetic id derived from the plan — stable across retries.
        return f"mock_price_{plan_code}"

    async def create_subscription(self, request: CreateSubscriptionRequest) -> ProviderSubscription:
        # Deterministic synthetic id so a retried create is idempotent locally.
        return ProviderSubscription(
            external_subscription_id=f"mock_sub_{request.organization_id}",
            status="trialing" if request.trial_days > 0 else "active",
            plan_code=request.plan_code,
        )

    async def get_subscription(self, external_subscription_id: str) -> ProviderSubscription:
        return ProviderSubscription(
            external_subscription_id=external_subscription_id,
            status="active",
            plan_code="",
        )

    async def report_usage(self, report: UsageReport) -> None:
        # No-op: nothing to bill locally. Kept so the call site is identical to
        # the live adapter.
        return None

    def parse_webhook(self, headers: dict, body: bytes) -> BillingWebhookEvent | None:
        """Parse a dev JSON envelope:
        ``{"id": "...", "type": "...", "subscription": "...", "status": "..."}``.
        Returns ``None`` on malformed input (the route then 204s silently)."""
        try:
            payload = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
        event_id = payload.get("id")
        event_type = payload.get("type")
        if not event_id or not event_type:
            return None
        return BillingWebhookEvent(
            event_id=str(event_id),
            event_type=str(event_type),
            external_subscription_id=payload.get("subscription"),
            status=payload.get("status"),
        )

    async def test_connection(self) -> bool:
        return True
