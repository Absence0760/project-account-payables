"""Refinitiv (LSEG) World-Check One adapter — skeleton.

Refinitiv's World-Check One API screens an entity against the
World-Check risk-intelligence database: sanctions, PEP, law-enforcement
watchlists, and adverse-media (negative-news) profiles. Auth is an
HMAC-signed request or an OAuth bearer token, depending on the gateway;
this skeleton uses a bearer key for clarity.

API: https://developers.lseg.com/ (World-Check One)

Working skeleton — the request shape and the response parsing follow
the published World-Check One contract, but a live API key must be set
in `Organization.settings.compliance.sanctions.api_key` before
`screen_vendor` will call out. Without a key the adapter raises
RuntimeError on the first call (fail-closed: a missing secret is never
treated as a clear screen). NOT the default; deployments select it
explicitly via `provider: "refinitiv"`.
"""

from __future__ import annotations

import logging
from decimal import Decimal

import httpx

from app.services.sanctions_adapters.base import ScreeningResult
from app.services.sanctions_adapters.dispatcher import register_sanctions_adapter

logger = logging.getLogger(__name__)

_BASE_URL = "https://api-worldcheck.refinitiv.com"


@register_sanctions_adapter("refinitiv")
class RefinitivAdapter:
    provider_name = "refinitiv"

    def __init__(self, config: dict | None = None):
        cfg = config or {}
        self.api_key: str = cfg.get("api_key", "")
        self.timeout = float(cfg.get("timeout_seconds", 10.0))
        # World-Check One scopes a screen to a case "group"; the
        # caller supplies it in prod. Optional in the skeleton.
        self.group_id: str | None = cfg.get("group_id")

    async def screen_vendor(
        self,
        *,
        vendor_name: str,
        vendor_country: str | None,
        vendor_tax_id: str | None = None,
        beneficial_owners: list[dict] | None = None,
    ) -> ScreeningResult:
        if not self.api_key:
            raise RuntimeError("refinitiv adapter requires `api_key` in compliance config")

        # World-Check One "screen an entity" request — name + entity
        # type + optional country, returning categorised results.
        body: dict = {
            "groupId": self.group_id,
            "entityType": "ORGANISATION",
            "providerTypes": ["WATCHLIST", "MEDIA_CHECK"],
            "caseScreeningState": {},
            "name": vendor_name,
        }
        if vendor_country:
            body["nationality"] = vendor_country.upper()

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{_BASE_URL}/v2/cases/screeningRequest", json=body, headers=headers
            )
            response.raise_for_status()
            payload = response.json()

        return self._parse(payload)

    def _parse(self, payload: dict) -> ScreeningResult:
        """Map the documented World-Check One response to a verdict.

        Documented shape (skeleton):
            {
              "results": [
                {"categories": [{"name": "SANCTIONS" | "PEP" | "ADVERSE-MEDIA"}], ...},
                ...
              ]
            }

        A `SANCTIONS` category is the hard refusal. PEP / adverse-media
        only → `review_required`. No results → clear. Only the
        PII-free category taxonomy is surfaced.
        """
        results = payload.get("results") or []
        if not results:
            return ScreeningResult(
                provider=self.provider_name,
                result="clear",
                risk_score=Decimal("0.00"),
                raw_response=payload,
            )

        raw_cats: set[str] = set()
        for r in results:
            for c in r.get("categories") or []:
                name = (c.get("name") or "").strip().lower()
                if name:
                    raw_cats.add(name)
        # Normalise World-Check's "adverse-media" to our taxonomy label.
        categories = tuple(sorted("adverse_media" if c == "adverse-media" else c for c in raw_cats))

        if "sanctions" in raw_cats:
            return ScreeningResult(
                provider=self.provider_name,
                result="match",
                matched_list="WORLDCHECK_SANCTIONS",
                risk_score=Decimal("95.00"),
                raw_response=payload,
                categories=categories,
            )

        return ScreeningResult(
            provider=self.provider_name,
            result="review_required",
            matched_list=",".join(c.upper() for c in categories) or "WORLDCHECK_REVIEW",
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
