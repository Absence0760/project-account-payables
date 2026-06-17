"""Dow Jones Risk & Compliance adapter — skeleton.

Dow Jones exposes a Risk & Compliance screening API (the "RiskCenter"
family) that screens an entity against sanctions, PEP, and
adverse-media (negative-news) datasets in one call. Auth is via an
OAuth bearer token / API key on every request.

API: https://developer.dowjones.com/ (Risk & Compliance)

This adapter ships as a working skeleton — the request shape and the
response parsing both follow the published API contract, but a live
API key must be set in
`Organization.settings.compliance.sanctions.api_key` before
`screen_vendor` will actually call out. Without a key the adapter
raises RuntimeError on the first call (same fail-closed pattern as the
ComplyAdvantage / OXR FX adapters — a missing secret is never silently
treated as a clear screen). It is NOT the default; deployments select
it explicitly via `provider: "dowjones"`.
"""

from __future__ import annotations

import logging
from decimal import Decimal

import httpx

from app.services.sanctions_adapters.base import ScreeningResult
from app.services.sanctions_adapters.dispatcher import register_sanctions_adapter

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.dowjones.com"


@register_sanctions_adapter("dowjones")
class DowJonesAdapter:
    provider_name = "dowjones"

    def __init__(self, config: dict | None = None):
        cfg = config or {}
        self.api_key: str = cfg.get("api_key", "")
        self.timeout = float(cfg.get("timeout_seconds", 10.0))
        # Dow Jones lets the caller scope which datasets to screen.
        # Default to the full risk picture: sanctions + PEP +
        # adverse-media (negative news).
        self.content_sets: list[str] = list(
            cfg.get("content_sets") or ["sanctions", "pep", "adverse-media"]
        )

    async def screen_vendor(
        self,
        *,
        vendor_name: str,
        vendor_country: str | None,
        vendor_tax_id: str | None = None,
        beneficial_owners: list[dict] | None = None,
    ) -> ScreeningResult:
        if not self.api_key:
            raise RuntimeError("dowjones adapter requires `api_key` in compliance config")

        # Dow Jones RiskCenter screening request — name + optional
        # country filter, screened across the configured content sets.
        body: dict = {
            "data": {
                "type": "risk-entity-screening-cases",
                "attributes": {
                    "search-term": vendor_name,
                    "content-set": self.content_sets,
                },
            }
        }
        if vendor_country:
            body["data"]["attributes"]["country"] = vendor_country.upper()

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{_BASE_URL}/riskentities/screening", json=body, headers=headers
            )
            response.raise_for_status()
            payload = response.json()

        return self._parse(payload)

    def _parse(self, payload: dict) -> ScreeningResult:
        """Map the documented Dow Jones response contract to a verdict.

        Documented shape (skeleton):
            {
              "data": {
                "attributes": {
                  "matches": [
                    {"match-type": "sanctions" | "pep" | "adverse-media", ...},
                    ...
                  ]
                }
              }
            }

        A `sanctions` match is the hard refusal (highest severity). PEP
        / adverse-media-only hits are `review_required`. No matches →
        clear. We surface only the match-type taxonomy (PII-free) in
        `categories` + `matched_list`, never the raw match detail.
        """
        attrs = (payload.get("data") or {}).get("attributes") or {}
        matches = attrs.get("matches") or []

        if not matches:
            return ScreeningResult(
                provider=self.provider_name,
                result="clear",
                risk_score=Decimal("0.00"),
                raw_response=payload,
            )

        types = {(m.get("match-type") or "").strip().lower() for m in matches}
        types.discard("")
        # Normalise Dow Jones' "adverse-media" to our taxonomy label.
        categories = tuple(sorted("adverse_media" if t == "adverse-media" else t for t in types))

        if "sanctions" in types:
            return ScreeningResult(
                provider=self.provider_name,
                result="match",
                matched_list="DOWJONES_SANCTIONS",
                risk_score=Decimal("95.00"),
                raw_response=payload,
                categories=categories,
            )

        return ScreeningResult(
            provider=self.provider_name,
            result="review_required",
            matched_list=",".join(c.upper() for c in categories) or "DOWJONES_REVIEW",
            risk_score=Decimal("60.00"),
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
