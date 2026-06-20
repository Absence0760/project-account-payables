"""Mock vendor-enrichment adapter — deterministic, no network, no credential.

The local-first default: ``pnpm dev`` and the whole test suite run against this
with no cloud account. Output is a pure deterministic function of the input
query, so two calls for the same vendor return byte-identical firmographics —
seed-friendly and assertable.

A vendor whose (lower-cased, trimmed) name appears in the built-in no-match
fixture set returns ``matched=False`` so a "vendor we couldn't enrich" path is
exercisable. Everything else gets a plausible-but-obviously-synthetic record
derived from the name hash (the legal name carries an ``(MOCK)`` marker so a
misconfigured prod-pointing-at-mock instance produces clearly fake data, not
realistic-looking firmographics).
"""

from __future__ import annotations

import hashlib

from app.services.enrichment_adapters.base import (
    VendorEnrichmentQuery,
    VendorFirmographics,
)
from app.services.enrichment_adapters.dispatcher import register_enrichment_adapter
from app.services.vendor_consolidation import mask_tax_id

# Names that deterministically return a no-match — lets tests exercise the
# "couldn't enrich this vendor" branch without a real provider.
_DEFAULT_NO_MATCH: frozenset[str] = frozenset(
    {
        "unknown vendor fixture",
        "no match test co",
    }
)

# Synthetic industry / SIC pairs, picked by name hash so the value is stable.
_INDUSTRIES: tuple[tuple[str, str, str], ...] = (
    ("Computer Programming Services", "7371", "541511"),
    ("Office Supplies", "5112", "453210"),
    ("Management Consulting Services", "8742", "541611"),
    ("Commercial Printing", "2752", "323111"),
    ("Freight Transportation", "4731", "488510"),
)


@register_enrichment_adapter("mock")
class MockEnrichmentAdapter:
    provider_name = "mock"

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        overrides = self.config.get("mock_no_match") or []
        self._no_match = _DEFAULT_NO_MATCH | {s.lower().strip() for s in overrides}

    async def enrich_vendor(self, query: VendorEnrichmentQuery) -> VendorFirmographics:
        name = (query.vendor_name or "").strip()
        name_key = name.lower()

        if not name or name_key in self._no_match:
            return VendorFirmographics(provider=self.provider_name, matched=False)

        # Deterministic synthetic data from a stable hash of the name.
        digest = hashlib.sha256(name_key.encode("utf-8")).hexdigest()
        h = int(digest[:8], 16)

        industry, sic, naics = _INDUSTRIES[h % len(_INDUSTRIES)]
        employees = 5 + (h % 4995)  # 5..4999, stable
        year_founded = 1950 + (h % 73)  # 1950..2022
        revenue = str(employees * 250_000)  # crude headcount-scaled figure
        domain = query.domain or f"{name_key.replace(' ', '')[:24] or 'vendor'}.example"
        country = query.vendor_country or "US"

        return VendorFirmographics(
            provider=self.provider_name,
            matched=True,
            legal_name=f"{name} (MOCK)",
            address="1 Mock Plaza, Suite 100",
            country=country,
            industry=industry,
            sic_code=sic,
            naics_code=naics,
            employee_count=employees,
            annual_revenue=revenue,
            website=f"https://{domain}",
            duns_number=f"{h % 1_000_000_000:09d}",
            year_founded=year_founded,
            # We never echo a raw tax id — only a masked form when one was given.
            tax_id_masked=mask_tax_id(query.vendor_tax_id),
            confidence=80 + (h % 20),  # 80..99, stable
            extra={"source": "mock", "deterministic": True},
        )

    async def test_connection(self) -> bool:
        return True
