"""Tax-rate adapter interface.

A tax-rate adapter answers one question: *what consumption-tax rate
applies to a supply in this jurisdiction?* — returning a percent as a
``Decimal``. The mock resolves rates deterministically from the
country-rules engine; cloud adapters (Avalara, TaxJar) call out to a
SaaS rate API. Same registry shape as ``fx_adapters`` / ``sanctions_adapters``.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True)
class TaxRateResult:
    """A resolved tax rate for a jurisdiction.

    ``rate`` is a percent (``Decimal("20")`` = 20%). ``regime`` echoes the
    country-rules regime ("vat" | "gst" | "sales_tax" | "none") so the
    caller knows which compute path to use. ``region`` is the optional
    sub-national area (US state, CA province, IN state) the rate applies to.
    """

    country_code: str
    region: str | None
    rate: Decimal
    regime: str
    rate_category: str
    provider: str


class TaxRateAdapter(Protocol):
    """The minimum contract every tax-rate provider must satisfy."""

    provider_name: str

    async def get_rate(
        self,
        country_code: str,
        *,
        region: str | None = None,
        rate_category: str | None = None,
    ) -> TaxRateResult:
        """Resolve the applicable rate.

        ``rate_category`` selects a non-standard rate ("reduced", "zero",
        "slab_5", ...) defined in the country-rules table; ``None`` means
        the standard rate. Implementations must raise on an unknown country
        rather than returning a zero rate, which would under-collect.
        """
        ...

    async def test_connection(self) -> bool:
        """Cheapest possible probe (auth check / single lookup)."""
        ...
