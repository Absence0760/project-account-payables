"""TIN-validation adapters — pluggable taxpayer-ID validation providers.

A TIN is an EIN (``NN-NNNNNNN``) or SSN/ITIN (``NNN-NN-NNNN``). Before
filing a 1099 the payer should confirm the vendor's TIN + legal name match
IRS records. Two layers, both behind one interface:

  - offline structural validation (``format_rules``) — free, deterministic,
    catches typos and never-issued numbers;
  - online IRS TIN-match — a partner API call (Tax1099) that proves the TIN
    is assigned to the named entity.

Same registry pattern as the other adapter families::

    @register_tin_validation_adapter("my_provider")
    class MyAdapter(TINValidationAdapter):
        async def validate(self, *, tin, legal_name=None, tin_type_hint=None): ...
        async def test_connection(self) -> bool: ...

Registered: ``mock`` (offline format-only, the local-first default),
``tax1099`` (IRS TIN-match skeleton — live key required).

Results never carry the raw TIN — only the last-4 + verdict — so a TIN can
never leak into a log line or error body.
"""

from app.services.tin_validation_adapters.base import (
    TINValidationAdapter,
    TINValidationResult,
)
from app.services.tin_validation_adapters.dispatcher import (
    get_tin_validation_adapter,
    register_tin_validation_adapter,
)

__all__ = [
    "TINValidationAdapter",
    "TINValidationResult",
    "get_tin_validation_adapter",
    "register_tin_validation_adapter",
]
