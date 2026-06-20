"""External vendor-enrichment adapter contract.

A vendor-enrichment adapter looks a vendor up in an external firmographics
provider (Dun & Bradstreet, Clearbit, ...) and returns a normalised
``VendorFirmographics`` record — legal name, registered address, industry /
SIC, employee count, website, ... — so an AP steward can *review* and selectively
apply the data. The result is **advisory / suggestion-only**: nothing here writes
back onto the ``Vendor`` row. The API layer surfaces it; a human decides.

Local-first invariant: the default adapter (`mock`) is deterministic and makes
no network call and needs no credential — `pnpm dev` runs against it. Real
providers (`dun_bradstreet`, `clearbit`) FAIL CLOSED without a per-org API key
(no hardcoded fallback secret).

PII: a vendor's ``tax_id`` is an INPUT we may pass to a provider, but it is never
echoed back in the response (only a masked ``***<last4>`` ever leaves the
service), and it never enters a log line — same posture as the consolidation
surface in ``services/vendor_consolidation``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class VendorEnrichmentQuery:
    """Identity payload handed to a provider to look a vendor up.

    Every field is optional bar ``vendor_name`` — providers match on whatever
    they're given. ``tax_id`` is a strong match key for D&B (it maps to a DUNS),
    but it is an INPUT only: it never comes back in the response.
    """

    vendor_name: str
    vendor_country: str | None = None
    vendor_tax_id: str | None = None
    domain: str | None = None  # website / email domain — Clearbit's primary key


@dataclass(frozen=True)
class VendorFirmographics:
    """Normalised firmographic record returned by an enrichment provider.

    Every field is optional — a provider returns only what it knows, and a
    no-match returns ``matched=False`` with everything ``None``. ``confidence``
    is the provider's 0..100 match confidence when available.

    Strictly advisory: the API layer presents this for a human to review and
    selectively apply; it is NEVER written back onto the ``Vendor`` row by the
    enrichment path. No raw ``tax_id`` is ever stored on this record — only the
    masked ``tax_id_masked`` (``***<last4>``) when the provider echoes one.
    """

    provider: str
    matched: bool
    legal_name: str | None = None
    address: str | None = None
    country: str | None = None
    industry: str | None = None
    sic_code: str | None = None
    naics_code: str | None = None
    employee_count: int | None = None
    annual_revenue: str | None = None  # string-Decimal / free text — never a float
    website: str | None = None
    duns_number: str | None = None
    year_founded: int | None = None
    tax_id_masked: str | None = None  # ***6789 — never the full id
    confidence: int | None = None  # 0..100 when the provider scores the match
    # Provider-specific extras a steward might find useful. PII-free taxonomy /
    # firmographic labels only — never DOB / passport / full address beyond the
    # registered business address above.
    extra: dict = field(default_factory=dict)


class EnrichmentNotConfigured(RuntimeError):
    """Raised by a real adapter asked to run without a credential.

    The API layer maps this to a 422 with a PII-free message (mirrors the
    ``BillingNotConfigured`` fail-closed posture). The mock adapter never raises
    it — local dev always works.
    """


class VendorEnrichmentAdapter(Protocol):
    """Minimum contract every external enrichment provider must satisfy."""

    provider_name: str

    async def enrich_vendor(self, query: VendorEnrichmentQuery) -> VendorFirmographics:
        """Look the vendor up and return normalised firmographics.

        A no-match returns ``VendorFirmographics(matched=False)`` — not an
        error. A missing credential on a real provider raises
        ``EnrichmentNotConfigured`` (fail closed)."""
        ...

    async def test_connection(self) -> bool:
        """Cheapest available probe (auth check). True on success."""
        ...
