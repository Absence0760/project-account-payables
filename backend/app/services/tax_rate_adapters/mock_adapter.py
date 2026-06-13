"""Mock tax-rate adapter — deterministic rates from the rules engine.

The default for local dev, CI, and any tenant that hasn't configured a
cloud rate provider. It reads the standard / category rate straight from
``country_rules.COUNTRY_RULES`` so the rates are predictable and tests can
assert exact numbers (UK 20%, AU GST 10%, IN GST 18%, CA federal GST 5%).

Per-tenant overrides can be injected via ``tax_config["mock_rates"]`` —
a ``{country_code: percent}`` map that shadows the standard rate, letting
a test simulate "this tenant negotiated a special rate" without touching
the shared rules table.
"""

from __future__ import annotations

from decimal import Decimal

from app.services.international_tax.country_rules import get_country_rule
from app.services.tax_rate_adapters.base import TaxRateResult
from app.services.tax_rate_adapters.dispatcher import register_tax_rate_adapter


@register_tax_rate_adapter("mock")
class MockTaxRateAdapter:
    provider_name = "mock"

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        overrides = self.config.get("mock_rates") or {}
        self._overrides: dict[str, Decimal] = {
            k.upper(): Decimal(str(v)) for k, v in overrides.items()
        }

    async def get_rate(
        self,
        country_code: str,
        *,
        region: str | None = None,
        rate_category: str | None = None,
    ) -> TaxRateResult:
        # `get_country_rule` raises UnknownCountry for unconfigured codes —
        # we propagate rather than defaulting to zero (would under-collect).
        rule = get_country_rule(country_code)
        code = rule.country_code

        if rate_category:
            if rate_category not in rule.rate_categories:
                raise ValueError(f"Unknown rate category {rate_category!r} for {code}")
            rate = rule.rate_categories[rate_category]
            category = rate_category
        elif code in self._overrides:
            rate = self._overrides[code]
            category = "standard"
        else:
            rate = rule.standard_rate
            category = "standard"

        return TaxRateResult(
            country_code=code,
            region=region,
            rate=rate,
            regime=rule.regime,
            rate_category=category,
            provider=self.provider_name,
        )

    async def test_connection(self) -> bool:
        return True
