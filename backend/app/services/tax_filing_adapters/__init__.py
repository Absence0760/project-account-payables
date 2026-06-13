"""1099 e-filing adapters — pluggable filing providers.

Submits a tax year's 1099-NEC / 1099-MISC forms to a filing partner (which
transmits to the IRS FIRE/IRIS). Same registry pattern as the other adapter
families::

    @register_tax_filing_adapter("my_provider")
    class MyAdapter(TaxFilingAdapter):
        async def submit_batch(self, *, tax_year, forms, idempotency_key): ...
        async def test_connection(self) -> bool: ...

Registered: ``mock`` (offline, deterministic, idempotent — the local-first
default) and ``tax1099`` (real partner skeleton — live key required).

Submissions are idempotent on the caller-supplied ``idempotency_key``; the
result carries counts + a confirmation number, never a TIN.
"""

from app.services.tax_filing_adapters.base import (
    FilingBatchResult,
    FilingFormPayload,
    FilingFormResult,
    TaxFilingAdapter,
)
from app.services.tax_filing_adapters.dispatcher import (
    get_tax_filing_adapter,
    register_tax_filing_adapter,
)

__all__ = [
    "FilingBatchResult",
    "FilingFormPayload",
    "FilingFormResult",
    "TaxFilingAdapter",
    "get_tax_filing_adapter",
    "register_tax_filing_adapter",
]
