"""C2FO supplier-financing adapter — skeleton.

C2FO (https://c2fo.com) runs a real supply-chain-finance marketplace:
buyers offer early payment on approved invoices and suppliers bid a
discount to get paid sooner, funded by C2FO's capital. Their partner
API exposes invoice eligibility, dynamic-discount quoting, and a
funding-request endpoint; auth is an API key + account id on every
request.

API: https://www.c2fo.com/ (partner integration docs are gated behind
a signed agreement — live credentials required).

This adapter ships as a SKELETON only: it pins the intended shape but
does NOT make real HTTP calls. It FAILS CLOSED — without an `api_key`
in `Organization.settings.financing` every method raises (quote /
funding) or returns False (`test_connection`), exactly like the
`complyadvantage` / `dowjones` sanctions skeletons. There is no
hardcoded key fallback (project invariant — secrets via sops + KMS,
never a literal default). A live integration fills in the `httpx`
bodies behind the credential guard.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal

from app.services.financing_adapters.base import FinancingFundingResult, FinancingQuote
from app.services.financing_adapters.dispatcher import register_financing_adapter

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.c2fo.com"


@register_financing_adapter("c2fo")
class C2FOAdapter:
    provider_name = "c2fo"

    def __init__(self, config: dict | None = None):
        cfg = config or {}
        # No hardcoded fallback — a real key only ever arrives via
        # sops-decrypted org config in a deployed env.
        self.api_key: str = cfg.get("api_key", "")
        self.account_id: str = cfg.get("account_id", "")
        self.timeout = float(cfg.get("timeout_seconds", 10.0))

    def _require_key(self) -> None:
        if not self.api_key:
            raise RuntimeError(
                "c2fo financing adapter requires `api_key` in financing config"
            )

    async def quote(
        self,
        *,
        invoice_amount: Decimal,
        currency: str,
        due_date: date,
        vendor_name: str,
        vendor_country: str | None = None,
    ) -> FinancingQuote:
        # Fail closed: no key → no quote. The orchestrator surfaces this
        # as an unconfigured-provider error rather than a fake offer.
        self._require_key()
        # Live implementation: POST {_BASE_URL}/v1/markets/.../offers with
        # the invoice + account, parse the returned discount + funding
        # dates into a FinancingQuote. Not implemented in the skeleton.
        raise NotImplementedError(
            "c2fo quote is a skeleton — live partner API integration required"
        )

    async def request_funding(
        self,
        *,
        quote: FinancingQuote,
        idempotency_key: str,
    ) -> FinancingFundingResult:
        self._require_key()
        # Live implementation: POST {_BASE_URL}/v1/.../fundings with the
        # accepted offer id + Idempotency-Key header, parse the funded
        # position into a FinancingFundingResult. Not implemented.
        raise NotImplementedError(
            "c2fo request_funding is a skeleton — live partner API integration required"
        )

    async def test_connection(self) -> bool:
        # Fail closed without a credential; never raise from the probe.
        if not self.api_key:
            return False
        # Live implementation: cheap authenticated GET (account ping).
        return False
