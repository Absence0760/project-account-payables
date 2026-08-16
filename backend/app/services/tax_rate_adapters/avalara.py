"""Avalara AvaTax tax-rate adapter — skeleton (live key required).

Avalara's AvaTax REST API resolves rates by address / jurisdiction. This
adapter is a structural placeholder following the same pattern as the
``complyadvantage`` sanctions skeleton: it documents the request shape and
auth, but raises until a real account + key is wired up so it can never
silently return a wrong rate in production.

API: https://developer.avalara.com/api-reference/avatax/rest/v2/
Auth: HTTP Basic with account-id:license-key.
"""

from __future__ import annotations

import logging

from app.services.tax_rate_adapters.base import TaxRateResult
from app.services.tax_rate_adapters.dispatcher import register_tax_rate_adapter

logger = logging.getLogger(__name__)


@register_tax_rate_adapter("avalara")
class AvalaraTaxRateAdapter:
    provider_name = "avalara"

    def __init__(self, config: dict | None = None):
        cfg = config or {}
        self.account_id: str = cfg.get("account_id", "")
        self.license_key: str = cfg.get("api_key", "")
        self.base_url: str = cfg.get("base_url", "https://rest.avatax.com")
        self.timeout = float(cfg.get("timeout_seconds", 8.0))

    async def get_rate(
        self,
        country_code: str,
        *,
        region: str | None = None,
        rate_category: str | None = None,
    ) -> TaxRateResult:
        if not (self.account_id and self.license_key):
            raise RuntimeError(
                "avalara tax-rate adapter requires `account_id` + `api_key` in tax config"
            )
        # A real implementation POSTs to /api/v2/taxrates/byaddress (or
        # /transactions/create for line-level tax) and maps the response
        # `totalRate` to a TaxRateResult. Left unimplemented so a misconfig
        # surfaces loudly rather than returning a placeholder rate.
        raise NotImplementedError(
            "Avalara tax-rate adapter is a skeleton — implement the AvaTax "
            "byaddress call before enabling it in a deployed environment."
        )

    async def test_connection(self) -> bool:
        # Fail closed, like `qms_adapters/generic_qms`: credentials alone are not
        # a healthy connection while `get_rate` — the only method this contract
        # exists for — cannot answer. Reporting True here told an operator the
        # provider was ready and left the failure to surface on the first real
        # rate lookup, under-collecting or blocking tax at that point instead of
        # at configuration time. A live implementation returns True from a cheap
        # authenticated GET once `get_rate` is real.
        return False
