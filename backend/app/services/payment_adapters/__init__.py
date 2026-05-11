"""Payment processor adapters — unified interface for ACH/wire/RTP providers."""

# Import adapters so they self-register with the dispatcher.
from app.services.payment_adapters import mock_adapter as _mock  # noqa: F401
from app.services.payment_adapters import modern_treasury as _modern_treasury  # noqa: F401
from app.services.payment_adapters.base import (
    CorridorQuote,
    PaymentAdapter,
    PaymentPayload,
    PaymentResult,
    PaymentStatus,
    WebhookEvent,
)
from app.services.payment_adapters.dispatcher import (
    get_payment_adapter,
    list_available_providers,
    register_payment_adapter,
)

__all__ = [
    "CorridorQuote",
    "PaymentAdapter",
    "PaymentPayload",
    "PaymentResult",
    "PaymentStatus",
    "WebhookEvent",
    "get_payment_adapter",
    "list_available_providers",
    "register_payment_adapter",
]
