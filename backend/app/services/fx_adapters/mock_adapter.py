"""Mock FX adapter — deterministic rates for tests and local dev.

The mock returns predictable rates against USD as the pivot:
    USD → EUR  ≈ 0.92
    USD → GBP  ≈ 0.79
    USD → JPY  ≈ 154.0
    ...

Cross rates (non-USD → non-USD) are computed via USD: rate(A → B) =
rate(USD → B) / rate(USD → A). Same-currency requests always return
exactly 1.0.

Custom rates can be injected via `fx_config["mock_rates"]` so a test
can simulate market moves between invoice issue and payment time.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.services.fx_adapters.base import FXRate
from app.services.fx_adapters.dispatcher import register_fx_adapter

# Anchor rates against USD. Numbers are intentionally round so tests
# can do arithmetic by hand.
_DEFAULT_USD_RATES: dict[str, Decimal] = {
    "USD": Decimal("1.0000"),
    "EUR": Decimal("0.9200"),
    "GBP": Decimal("0.7900"),
    "JPY": Decimal("154.0000"),
    "CAD": Decimal("1.3700"),
    "AUD": Decimal("1.5200"),
    "CHF": Decimal("0.8800"),
    "SEK": Decimal("10.4000"),
    "NOK": Decimal("10.6000"),
    "DKK": Decimal("6.8500"),
    "PLN": Decimal("4.0000"),
    "SGD": Decimal("1.3400"),
    "HKD": Decimal("7.8000"),
    "INR": Decimal("83.5000"),
    "MXN": Decimal("17.0000"),
    "BRL": Decimal("5.0000"),
    "ZAR": Decimal("18.5000"),
    "AED": Decimal("3.6700"),
    "NZD": Decimal("1.6500"),
    "CNY": Decimal("7.2400"),
}


class _UnknownCurrency(ValueError):
    """Raised when a code isn't in the mock table — keeps the contract
    consistent with what a real provider would do."""


@register_fx_adapter("mock")
class MockFXAdapter:
    provider_name = "mock"

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        overrides = self.config.get("mock_rates") or {}
        # Caller-supplied overrides take precedence so tests can
        # simulate "EUR moved against USD between t0 and t1".
        self._usd_rates: dict[str, Decimal] = {
            **_DEFAULT_USD_RATES,
            **{k.upper(): Decimal(str(v)) for k, v in overrides.items()},
        }

    async def get_rate(self, source: str, target: str) -> FXRate:
        src = source.upper()
        tgt = target.upper()

        if src == tgt:
            return FXRate(
                source=src,
                target=tgt,
                rate=Decimal("1.0000"),
                as_of=datetime.now(UTC),
                provider=self.provider_name,
            )

        if src not in self._usd_rates:
            raise _UnknownCurrency(f"Unknown source currency: {src}")
        if tgt not in self._usd_rates:
            raise _UnknownCurrency(f"Unknown target currency: {tgt}")

        # rate(src → tgt) = rate(USD → tgt) / rate(USD → src)
        rate = (self._usd_rates[tgt] / self._usd_rates[src]).quantize(Decimal("0.000001"))
        return FXRate(
            source=src,
            target=tgt,
            rate=rate,
            as_of=datetime.now(UTC),
            provider=self.provider_name,
        )

    async def test_connection(self) -> bool:
        return True
