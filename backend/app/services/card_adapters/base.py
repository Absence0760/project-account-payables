"""Base card adapter interface and shared data types."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from decimal import Decimal


class CardStatus(enum.StrEnum):
    created = "created"
    sent = "sent"
    active = "active"
    charged = "charged"
    completed = "completed"
    expired = "expired"
    cancelled = "cancelled"
    declined = "declined"


@dataclass
class VirtualCardPayload:
    """Data needed to create a virtual card."""

    correlation_id: str
    invoice_id: str
    vendor_name: str
    vendor_email: str | None
    amount: Decimal
    currency: str = "USD"
    description: str | None = None
    expiry_days: int = 30
    metadata: dict | None = None


@dataclass
class CardResult:
    success: bool
    provider_card_id: str | None = None
    last_four: str | None = None
    message: str | None = None
    raw_response: dict | None = None


@dataclass
class CardDetails:
    """Full card details — only retrieved on explicit request."""

    card_number: str
    exp_month: int
    exp_year: int
    cvv: str
    last_four: str


class CardAdapter:
    """Base class for card provider integrations."""

    provider_name: str = "base"

    # Regions this adapter supports
    supported_regions: list[str] = []

    def __init__(self, config: dict):
        self.config = config

    async def create_card(self, payload: VirtualCardPayload) -> CardResult:
        """Create a single-use virtual card."""
        raise NotImplementedError

    async def get_card_details(self, provider_card_id: str) -> CardDetails:
        """Retrieve full card details (number, CVV) for sending to vendor."""
        raise NotImplementedError

    async def cancel_card(self, provider_card_id: str) -> bool:
        """Cancel/void an unused card."""
        raise NotImplementedError

    async def get_card_status(self, provider_card_id: str) -> CardStatus:
        """Check current card status from the provider."""
        raise NotImplementedError

    async def test_connection(self) -> bool:
        """Verify the provider connection is working."""
        raise NotImplementedError
