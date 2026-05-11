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

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.complyadvantage.com"


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
                "types": ["sanction", "warning", "fitness-probity", "pep"],
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

        # Response shape:
        # {
        #   "content": {"data": {"hits": [...], "total_hits": N}},
        # }
        # A 0-hit response is `clear`. Any sanction-typed hit is a
        # `match` (highest severity). A PEP-only hit is
        # `review_required`. Warning-only hits are also review.
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

        # Bucket hits by type to drive the verdict.
        types = set()
        for h in hits:
            for t in h.get("doc", {}).get("types") or []:
                types.add(t.lower())

        if "sanction" in types:
            return ScreeningResult(
                provider=self.provider_name,
                result="match",
                matched_list="OFAC/EU/UN/UK_SANCTION",
                risk_score=Decimal("95.00"),
                raw_response=payload,
            )

        # Anything else (PEP, warning, fitness-probity) goes to the
        # review queue — not auto-refused.
        return ScreeningResult(
            provider=self.provider_name,
            result="review_required",
            matched_list=",".join(sorted(types)) or "UNKNOWN",
            risk_score=Decimal("70.00"),
            raw_response=payload,
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
