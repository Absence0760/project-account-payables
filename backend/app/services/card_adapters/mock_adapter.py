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


@register_card_adapter("mock")
class MockCardAdapter(CardAdapter):
    provider_name = "mock"
    supported_regions = ["US", "UK", "ZA", "AU"]  # everywhere for testing

    async def create_card(self, payload: VirtualCardPayload) -> CardResult:
        await asyncio.sleep(0.05)
        card_id = f"mock_card_{uuid.uuid4().hex[:12]}"
        return CardResult(
            success=True,
            provider_card_id=card_id,
            last_four="4242",
            message="Mock card created",
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
