"""Payment processor adapters — unified interface for ACH/wire/RTP providers."""

# Import adapters so they self-register with the dispatcher.
from app.services.payment_adapters import checkeeper as _checkeeper  # noqa: F401
from app.services.payment_adapters import column as _column  # noqa: F401
from app.services.payment_adapters import dwolla as _dwolla  # noqa: F401
from app.services.payment_adapters import increase as _increase  # noqa: F401
from app.services.payment_adapters import mock_adapter as _mock  # noqa: F401
from app.services.payment_adapters import modern_treasury as _modern_treasury  # noqa: F401
from app.services.payment_adapters import stripe_treasury as _stripe_treasury  # noqa: F401
from app.services.payment_adapters.base import (
    BalanceResult,
    CorridorQuote,
    PaymentAdapter,
    PaymentPayload,
    PaymentResult,
    PaymentStatus,
    SettlementReport,
    WebhookEvent,
    exponent_for,
    minor_units_to_decimal,
    parse_amount,
    to_minor_units,
)
from app.services.payment_adapters.dispatcher import (
    get_payment_adapter,
    list_available_providers,
    register_payment_adapter,
)

__all__ = [
    "BalanceResult",
    "CorridorQuote",
    "PaymentAdapter",
    "PaymentPayload",
    "PaymentResult",
    "PaymentStatus",
    "SettlementReport",
    "WebhookEvent",
    "get_payment_adapter",
    "list_available_providers",
    "exponent_for",
    "minor_units_to_decimal",
    "to_minor_units",
    "parse_amount",
    "register_payment_adapter",
]
