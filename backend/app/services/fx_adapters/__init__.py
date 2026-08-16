"""FX rate adapters — pluggable foreign-exchange rate providers.

Adapters return mid-market rates; the orchestration layer applies any
spread / fee for the chosen corridor. The rate returned by `get_rate`
is what we *lock* on the Payment row at submission time, so a later
move in the market doesn't surprise the customer.

Same registry pattern as `card_adapters`, `extraction_adapters`,
`erp_adapters`:

    @register_fx_adapter("my_provider")
    class MyAdapter(FXAdapter):
        async def get_rate(self, source: str, target: str) -> FXRate: ...
        async def test_connection(self) -> bool: ...
"""

from app.services.fx_adapters.base import FXAdapter, FXRate
from app.services.fx_adapters.dispatcher import (
    UnknownFxProviderError,
    get_fx_adapter,
    register_fx_adapter,
)

__all__ = [
    "FXAdapter",
    "FXRate",
    "UnknownFxProviderError",
    "get_fx_adapter",
    "register_fx_adapter",
]
