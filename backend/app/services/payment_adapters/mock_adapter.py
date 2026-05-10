"""Mock payment adapter — for local dev and the test suite.

Behaves like a processor that immediately confirms every payment. Generates
a deterministic-shape reference (`MOCK-{method}-{8-char hex}`) so tests can
assert on the format without asserting on a specific UUID. Always reports
success.

This is the default adapter when no provider is configured (see dispatcher).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.services.payment_adapters.base import (
    PaymentAdapter,
    PaymentPayload,
    PaymentResult,
    PaymentStatus,
    WebhookEvent,
)
from app.services.payment_adapters.dispatcher import register_payment_adapter


@register_payment_adapter("mock")
class MockPaymentAdapter(PaymentAdapter):
    provider_name = "mock"
    supported_methods = ("ach", "wire", "check", "rtp", "virtual_card")

    async def create_payment(self, payload: PaymentPayload) -> PaymentResult:
        # Mock always settles immediately. Real processors take seconds to
        # days; the webhook handler is what completes them in production.
        provider_id = f"mock_pmt_{uuid.uuid4().hex[:12]}"
        reference = f"MOCK-{payload.method.upper()}-{uuid.uuid4().hex[:8].upper()}"
        return PaymentResult(
            success=True,
            status=PaymentStatus.completed,
            provider_payment_id=provider_id,
            reference=reference,
            raw_response={"mock": True, "correlation_id": payload.correlation_id},
        )

    async def get_payment_status(self, provider_payment_id: str) -> PaymentStatus:
        # Mock has no real lifecycle — anything we minted is "completed."
        return PaymentStatus.completed

    def parse_webhook(self, headers: dict, body: bytes) -> WebhookEvent | None:
        # Mock doesn't send webhooks; this exists so test fixtures can call
        # it directly to simulate a status transition.
        try:
            import json

            payload = json.loads(body or b"{}")
        except ValueError:
            return None
        provider_payment_id = payload.get("provider_payment_id")
        status = payload.get("status")
        if not provider_payment_id or status not in PaymentStatus.__members__:
            return None
        return WebhookEvent(
            provider_payment_id=provider_payment_id,
            status=PaymentStatus(status),
            reference=payload.get("reference"),
            failure_reason=payload.get("failure_reason"),
            occurred_at=datetime.now(UTC).isoformat(),
            raw=payload,
        )

    async def test_connection(self) -> bool:
        return True

    async def void_payment(self, provider_payment_id: str) -> bool:
        # Mock accepts every void — gives test fixtures a working
        # `voided_upstream` audit outcome without stubbing any HTTP.
        return True
