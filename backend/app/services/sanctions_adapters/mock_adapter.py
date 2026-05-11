"""Mock sanctions adapter — deterministic for tests and local dev.

Returns `clear` for any name not in the built-in test blocklist, and
`match` for a small set of fixture names. Tests can override the
blocklist via `compliance_config["mock_blocklist"]` to simulate hits.
"""

from __future__ import annotations

from decimal import Decimal

from app.services.sanctions_adapters.base import ScreeningResult
from app.services.sanctions_adapters.dispatcher import register_sanctions_adapter

# Fixture names that always hit. Chosen to look like the real OFAC
# SDN test cases (the canonical "John Doe Test SDN" pattern) so a
# misconfigured prod-pointing-at-mock instance produces clearly
# fake-looking hits rather than realistic-looking ones.
_DEFAULT_BLOCKLIST: frozenset[str] = frozenset(
    {
        "sanctioned test entity",
        "ofac sdn fixture",
        "blocked party llc",
    }
)

# High-risk jurisdictions per FATF + OFAC. Vendors registered in
# these countries get a `review_required` even if the name doesn't
# match a list — the AP team triages. Treat as case-insensitive
# ISO-3166 alpha-2 codes.
_HIGH_RISK_COUNTRIES: frozenset[str] = frozenset(
    {
        "IR",  # Iran
        "KP",  # North Korea
        "SY",  # Syria
        "CU",  # Cuba
        "RU",  # Russia (sectoral sanctions)
        "BY",  # Belarus
        "MM",  # Myanmar
        "VE",  # Venezuela
        "AF",  # Afghanistan
    }
)


@register_sanctions_adapter("mock")
class MockSanctionsAdapter:
    provider_name = "mock"

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        overrides = self.config.get("mock_blocklist") or []
        self._blocklist = _DEFAULT_BLOCKLIST | {s.lower().strip() for s in overrides}
        high_risk_overrides = self.config.get("mock_high_risk_countries") or []
        self._high_risk = _HIGH_RISK_COUNTRIES | {s.upper().strip() for s in high_risk_overrides}

    async def screen_vendor(
        self,
        *,
        vendor_name: str,
        vendor_country: str | None,
        vendor_tax_id: str | None = None,
        beneficial_owners: list[dict] | None = None,
    ) -> ScreeningResult:
        name_key = (vendor_name or "").strip().lower()
        country = (vendor_country or "").strip().upper() or None

        # Direct name hit → match (90 risk score).
        if name_key in self._blocklist:
            return ScreeningResult(
                provider=self.provider_name,
                result="match",
                matched_list="MOCK_TEST_SDN",
                risk_score=Decimal("90.00"),
                raw_response={"hit": name_key, "list": "MOCK_TEST_SDN"},
            )

        # Beneficial owners hit → match too.
        for owner in beneficial_owners or []:
            owner_name = (owner.get("name") or "").strip().lower()
            if owner_name and owner_name in self._blocklist:
                return ScreeningResult(
                    provider=self.provider_name,
                    result="match",
                    matched_list="MOCK_TEST_SDN_OWNER",
                    risk_score=Decimal("90.00"),
                    raw_response={"hit": owner_name, "via": "beneficial_owner"},
                )

        # High-risk country → review (60 risk score). Not a refusal,
        # just a flag for the AP team.
        if country and country in self._high_risk:
            return ScreeningResult(
                provider=self.provider_name,
                result="review_required",
                matched_list=f"FATF_HIGH_RISK_{country}",
                risk_score=Decimal("60.00"),
                raw_response={"country": country, "reason": "high_risk_jurisdiction"},
            )

        return ScreeningResult(
            provider=self.provider_name,
            result="clear",
            risk_score=Decimal("0.00"),
        )

    async def test_connection(self) -> bool:
        return True
