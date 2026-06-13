"""Tax-rate adapters — pluggable jurisdiction rate providers.

Adapters resolve a consumption-tax rate (VAT / GST / sales tax) for a
country + optional region. The ``mock`` default reads deterministic rates
from the country-rules engine so local dev + CI need no cloud account;
``avalara`` and ``taxjar`` are skeletons for real SaaS rate APIs.

Same registry pattern as ``fx_adapters`` / ``sanctions_adapters``::

    @register_tax_rate_adapter("my_provider")
    class MyAdapter(TaxRateAdapter):
        async def get_rate(self, country_code, *, region=None,
                           rate_category=None) -> TaxRateResult: ...
        async def test_connection(self) -> bool: ...
"""

from app.services.tax_rate_adapters.base import TaxRateAdapter, TaxRateResult
from app.services.tax_rate_adapters.dispatcher import (
    get_tax_rate_adapter,
    register_tax_rate_adapter,
)

__all__ = [
    "TaxRateAdapter",
    "TaxRateResult",
    "get_tax_rate_adapter",
    "register_tax_rate_adapter",
]
