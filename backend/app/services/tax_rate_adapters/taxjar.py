"""TaxJar tax-rate adapter — skeleton (live key required).

TaxJar's /v2/rates and /v2/taxes endpoints resolve rates by location. Like
the Avalara adapter this is a structural placeholder that raises until a
real account + token is configured, so it can never silently return a wrong
rate in production.

API: https://developer.taxjar.com/api/reference/
Auth: Bearer token.
"""

from __future__ import annotations

import logging

from app.services.tax_rate_adapters.base import TaxRateResult
from app.services.tax_rate_adapters.dispatcher import register_tax_rate_adapter

logger = logging.getLogger(__name__)


@register_tax_rate_adapter("taxjar")
class TaxJarTaxRateAdapter:
    provider_name = "taxjar"

    def __init__(self, config: dict | None = None):
        cfg = config or {}
        self.api_token: str = cfg.get("api_key", "")
        self.base_url: str = cfg.get("base_url", "https://api.taxjar.com")
        self.timeout = float(cfg.get("timeout_seconds", 8.0))

    async def get_rate(
        self,
        country_code: str,
        *,
        region: str | None = None,
        rate_category: str | None = None,
    ) -> TaxRateResult:
        if not self.api_token:
            raise RuntimeError("taxjar tax-rate adapter requires `api_key` in tax config")
        # A real implementation GETs /v2/rates/{zip}?country=... and maps the
        # response `rate.combined_rate` to a TaxRateResult. Left unimplemented
        # so a misconfig surfaces loudly.
        raise NotImplementedError(
            "TaxJar tax-rate adapter is a skeleton — implement the /v2/rates "
            "call before enabling it in a deployed environment."
        )

    async def test_connection(self) -> bool:
        return bool(self.api_token)
