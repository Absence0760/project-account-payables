"""Mock card adapter for development and testing."""

import asyncio
import uuid

from app.services.card_adapters.base import (
    CardAdapter,
    CardDetails,
    CardResult,
    CardStatus,
    VirtualCardPayload,
)
from app.services.card_adapters.dispatcher import register_card_adapter

# Fixed namespace so a given idempotency key always maps to the same mock card
# id, in this process and the next one.
_MOCK_CARD_NAMESPACE = uuid.UUID("5f7f0e2e-2a1f-5c9d-9a0b-2f5f9d1c7a41")


@register_card_adapter("mock")
class MockCardAdapter(CardAdapter):
    provider_name = "mock"
    supported_regions = ["US", "UK", "ZA", "AU"]  # everywhere for testing

    async def create_card(self, payload: VirtualCardPayload) -> CardResult:
        await asyncio.sleep(0.05)
        if payload.idempotency_key:
            # Honour the idempotency key the way the real providers do: the same
            # key resolves to the SAME card instead of minting a second one. A
            # deterministic derivation (rather than an in-process cache) means
            # the guarantee also holds across a process restart, so local-first
            # dev and tests can exercise the retry path with no provider.
            derived = uuid.uuid5(_MOCK_CARD_NAMESPACE, payload.idempotency_key)
            card_id = f"mock_card_{derived.hex[:12]}"
        else:
            card_id = f"mock_card_{uuid.uuid4().hex[:12]}"
        return CardResult(
            success=True,
            provider_card_id=card_id,
            last_four="4242",
            message="Mock card created",
            # Echoed so a caller/test can assert the key actually reached the
            # provider leg. PII-free — it is a derived UUID, not card data.
            raw_response={"idempotency_key": payload.idempotency_key},
        )

    async def get_card_details(self, provider_card_id: str) -> CardDetails:
        await asyncio.sleep(0.05)
        return CardDetails(
            card_number="4242424242424242",
            exp_month=12,
            exp_year=2027,
            cvv="123",
            last_four="4242",
        )

    async def cancel_card(self, provider_card_id: str) -> bool:
        await asyncio.sleep(0.05)
        # Idempotent by design: cancelling an already-closed card is SUCCESS, not
        # a failure — mirrors the real lithic/nium adapters, which treat a
        # 404/409 or an already-CLOSED/TERMINATED state as a confirmed cancel.
        return True

    async def get_card_status(self, provider_card_id: str) -> CardStatus:
        await asyncio.sleep(0.05)
        return CardStatus.active

    async def test_connection(self) -> bool:
        return True
