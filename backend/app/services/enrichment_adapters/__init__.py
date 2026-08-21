"""External vendor-enrichment adapters — pluggable firmographics providers
(Dun & Bradstreet, Clearbit, ...).

Same registry pattern as ``sanctions_adapters`` / ``fx_adapters`` /
``billing_adapters``. The default in local dev is ``mock`` (deterministic, no
network, no credential — the local-first invariant); production deployments set
``Organization.settings.enrichment.provider`` to one of the registered real
names plus its ``api_key`` (sops-backed). The real providers FAIL CLOSED on a
missing key (``EnrichmentNotConfigured``) — no hardcoded fallback secret.

Advisory only: the adapter returns firmographics for a steward to review and
selectively apply; the enrichment path never writes back onto the ``Vendor`` row.
See ``backend/docs/data-enrichment.md`` § External enrichment.
"""

from app.services.enrichment_adapters.base import (
    EnrichmentNotConfigured,
    VendorEnrichmentAdapter,
    VendorEnrichmentQuery,
    VendorFirmographics,
)
from app.services.enrichment_adapters.dispatcher import (
    UnknownEnrichmentProviderError,
    get_enrichment_adapter,
    list_available_providers,
    register_enrichment_adapter,
)

__all__ = [
    "EnrichmentNotConfigured",
    "UnknownEnrichmentProviderError",
    "VendorEnrichmentAdapter",
    "VendorEnrichmentQuery",
    "VendorFirmographics",
    "get_enrichment_adapter",
    "list_available_providers",
    "register_enrichment_adapter",
]
