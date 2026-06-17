"""Supplier-financing adapters — pluggable supply-chain-finance (SCF)
marketplaces that fund a supplier's early invoice payment.

A financier pays the supplier now (face value minus a fee) and the
buyer repays the financier at the invoice's original due date. Adapters
(a) quote early-payment terms and (b) request funding.

Same registry pattern as `fx_adapters`, `sanctions_adapters`,
`payment_adapters`. Default in local dev is `mock` (deterministic, no
network, no credential); production deployments configure
`Organization.settings.financing.provider` to a registered name
(today: `mock`, `c2fo` skeleton — live key required).
"""

from app.services.financing_adapters.base import (
    FinancingAdapter,
    FinancingFundingResult,
    FinancingQuote,
)
from app.services.financing_adapters.dispatcher import (
    get_financing_adapter,
    register_financing_adapter,
)

__all__ = [
    "FinancingAdapter",
    "FinancingFundingResult",
    "FinancingQuote",
    "get_financing_adapter",
    "register_financing_adapter",
]
