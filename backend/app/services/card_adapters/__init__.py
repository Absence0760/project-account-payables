"""Virtual card adapter package — unified interface for card providers."""

from app.services.card_adapters.base import (
    CardAdapter,
    CardDetails,
    CardResult,
    CardStatus,
    VirtualCardPayload,
)
from app.services.card_adapters.dispatcher import (
    UnknownCardProviderError,
    get_card_adapter,
)

__all__ = [
    "CardAdapter",
    "CardDetails",
    "CardResult",
    "CardStatus",
    "UnknownCardProviderError",
    "VirtualCardPayload",
    "get_card_adapter",
]
