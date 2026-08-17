"""ComplyAdvantage adapter — skeleton.

ComplyAdvantage exposes a JSON `searches` endpoint that returns hits
against OFAC SDN + EU consolidated + UN + UK HMT + PEP lists in one
call. Auth is via `Authorization: Token <key>` on every request.
Free-tier orgs have no production traffic; sandbox accounts work
identically.

API: https://docs.complyadvantage.com/api/

This adapter ships as a working skeleton — the request shape and the
response parsing both match the published API, but the live API key
needs to be set in `Organization.settings.compliance.sanctions.api_key`
before screen_vendor will actually call out. Without a key the
adapter falls back to raising RuntimeError on the first call (same
pattern as the OXR FX adapter), which the orchestrator surfaces as
`failure_reason="compliance_provider_unconfigured"`.
"""

from __future__ import annotations

import logging
from decimal import Decimal

import httpx

from app.services.sanctions_adapters.base import ScreeningResult
from app.services.sanctions_adapters.dispatcher import register_sanctions_adapter
from app.services.sanctions_categories import (
    CATEGORY_ADVERSE_MEDIA,
    CATEGORY_PEP,
    CATEGORY_SANCTIONS,
)

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.complyadvantage.com"

# The search types we ask ComplyAdvantage for. `adverse-media` is included
# because negative-news screening is part of what this module promises (see
# `backend/docs/vendor-risk-screening.md`) — a control that never asks for the
# signal it claims to screen for is a false assurance. It widens what comes
# back to `review_required`, which is the correct direction for a compliance
# gate: an adverse-media hit is "review the relationship", never an auto-block.
_SEARCH_TYPES = ["sanction", "warning", "fitness-probity", "pep", "adverse-media"]

# CA's own hit-type vocabulary → our PII-free taxonomy
# (`services/sanctions_categories`). Anything CA reports that isn't listed here
# is carried through with hyphens normalised to underscores rather than
# dropped — an unmapped label is still evidence, and the surfaces render an
# unknown one by de-underscoring it.
_TYPE_TO_CATEGORY = {
    "sanction": CATEGORY_SANCTIONS,
    "pep": CATEGORY_PEP,
    "adverse-media": CATEGORY_ADVERSE_MEDIA,
}


@register_sanctions_adapter("complyadvantage")
class ComplyAdvantageAdapter:
    provider_name = "complyadvantage"

    def __init__(self, config: dict | None = None):
        cfg = config or {}
        self.api_key: str = cfg.get("api_key", "")
        self.timeout = float(cfg.get("timeout_seconds", 10.0))
        # Default fuzzy threshold per CA's docs — 80 is the
        # provider-recommended cutoff for new searches.
        self.fuzziness: int = int(cfg.get("fuzziness", 80))

    async def screen_vendor(
        self,
        *,
        vendor_name: str,
        vendor_country: str | None,
        vendor_tax_id: str | None = None,
        beneficial_owners: list[dict] | None = None,
    ) -> ScreeningResult:
        if not self.api_key:
            raise RuntimeError("complyadvantage adapter requires `api_key` in compliance config")

        body = {
            "search_term": vendor_name,
            "fuzziness": self.fuzziness / 100.0,
            "filters": {
                "types": list(_SEARCH_TYPES),
            },
        }
        # CA accepts ISO country codes as an additional filter to cut
        # false positives — we send it when we have it.
        if vendor_country:
            body["filters"]["country_codes"] = [vendor_country.upper()]

        headers = {"Authorization": f"Token {self.api_key}"}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f"{_BASE_URL}/searches", json=body, headers=headers)
            response.raise_for_status()
            payload = response.json()

        return self._parse(payload)

    def _parse(self, payload: dict) -> ScreeningResult:
        """Map the documented `searches` response to a verdict + taxonomy.

        Response shape:
            {"content": {"data": {"hits": [...], "total_hits": N}}}

        A 0-hit response is `clear`. Any `sanction`-typed hit is a `match`
        (highest severity). Anything else — PEP, warning, fitness-probity,
        **adverse-media** — is `review_required`, not auto-refused.

        Split out of `screen_vendor` (mirroring the `dowjones` / `refinitiv`
        siblings) so the response contract is testable without a network call
        or a live key.
        """
        data = (payload.get("content") or {}).get("data") or {}
        hits = data.get("hits") or []
        total_hits = int(data.get("total_hits", 0))

        if total_hits == 0 or not hits:
            return ScreeningResult(
                provider=self.provider_name,
                result="clear",
                risk_score=Decimal("0.00"),
                raw_response=payload,
            )

        # Bucket hits by type to drive the verdict + the category taxonomy.
        types: set[str] = set()
        for h in hits:
            for t in h.get("doc", {}).get("types") or []:
                if isinstance(t, str) and t.strip():
                    types.add(t.strip().lower())

        categories = tuple(sorted({_TYPE_TO_CATEGORY.get(t, t.replace("-", "_")) for t in types}))

        if "sanction" in types:
            return ScreeningResult(
                provider=self.provider_name,
                result="match",
                matched_list="OFAC/EU/UN/UK_SANCTION",
                risk_score=Decimal("95.00"),
                raw_response=payload,
                categories=categories,
            )

        # Anything else (PEP, warning, fitness-probity, adverse-media) goes to
        # the review queue — not auto-refused.
        return ScreeningResult(
            provider=self.provider_name,
            result="review_required",
            matched_list=",".join(sorted(types)) or "UNKNOWN",
            risk_score=Decimal("70.00"),
            raw_response=payload,
            categories=categories,
        )

    async def test_connection(self) -> bool:
        try:
            await self.screen_vendor(
                vendor_name="connection_test_payload",
                vendor_country=None,
            )
        except Exception:  # noqa: BLE001
            return False
        return True
