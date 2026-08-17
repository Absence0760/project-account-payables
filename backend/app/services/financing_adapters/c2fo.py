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

**A credentialled call gets the documented "no offer" shape, not a
crash.** `base.FinancingAdapter.quote` says implementations "return an
ineligible `FinancingQuote` rather than raising when the provider
simply declines — a missing credential is the one case that may fail
closed (raise)". This skeleton used to `raise NotImplementedError`
from both `quote` and `request_funding` even with a key present, which
breaks that contract in the direction that costs the most: the family
has no production caller yet, so nothing fails today, and the first
one wired up would take a 500 from a path whose whole contract is that
it answers "not eligible" instead. The refusal now travels as
`eligible=False` / `funded=False` with the PII-free machine reason
`provider_not_implemented`, so a caller reads it exactly like a
declined offer, no money moves, and `test_connection` stays False —
the operator still learns at configuration time that this integration
cannot fund anything (the honest-probe rule
`tests/test_tax_rate_adapters.py` established for the tax-rate
skeletons, guarded registry-wide in
`tests/test_adapter_contract_integrity.py`).
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal

from app.services.financing_adapters.base import FinancingFundingResult, FinancingQuote
from app.services.financing_adapters.dispatcher import register_financing_adapter

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.c2fo.com"

_ZERO = Decimal("0.00")

#: PII-free machine reason carried on every refusal this skeleton returns.
#: Names the condition (the integration is unwritten), never the operator's
#: credential or the supplier — it reaches an API response and a log line.
REASON_NOT_IMPLEMENTED = "provider_not_implemented"


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
            raise RuntimeError("c2fo financing adapter requires `api_key` in financing config")

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
        # dates into a FinancingQuote. Until that lands, answer in the
        # contract's own vocabulary: an ineligible quote with zeroed money.
        # `funding_date` is None because nothing will ever be disbursed;
        # `repayment_date` is the caller's own due date, which is a fact
        # regardless of who funds it.
        logger.warning(
            "c2fo financing quote requested but the integration is a skeleton; "
            "returning an ineligible quote (%s)",
            REASON_NOT_IMPLEMENTED,
        )
        return FinancingQuote(
            provider=self.provider_name,
            eligible=False,
            discount_percent=_ZERO,
            fee_percent=_ZERO,
            funding_date=None,
            repayment_date=due_date,
            advance_amount=_ZERO,
            reason=REASON_NOT_IMPLEMENTED,
            raw_response={"reason": REASON_NOT_IMPLEMENTED},
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
        # position into a FinancingFundingResult. Until that lands, report
        # the un-funded outcome the result type already models. `status` is
        # "unavailable", NOT "declined" — no financier ever saw this request,
        # and a caller must not record a provider decision that never
        # happened. No money moves, so the repeat call is trivially
        # idempotent on `idempotency_key`.
        logger.warning(
            "c2fo financing funding requested but the integration is a skeleton; "
            "returning an unfunded result (%s)",
            REASON_NOT_IMPLEMENTED,
        )
        return FinancingFundingResult(
            provider=self.provider_name,
            funded=False,
            external_funding_id=None,
            advance_amount=_ZERO,
            fee_amount=_ZERO,
            status="unavailable",
            reason=REASON_NOT_IMPLEMENTED,
            raw_response={"reason": REASON_NOT_IMPLEMENTED},
        )

    async def test_connection(self) -> bool:
        # Fail closed without a credential; never raise from the probe.
        if not self.api_key:
            return False
        # Live implementation: cheap authenticated GET (account ping).
        return False
