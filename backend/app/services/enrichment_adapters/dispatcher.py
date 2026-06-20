"""External vendor-enrichment adapter dispatcher.

Same registry pattern as ``sanctions_adapters`` / ``fx_adapters`` / ``billing_adapters``.
Default in local dev is ``mock`` (deterministic, no network, no credential).
Production deployments set ``Organization.settings.enrichment.provider`` to a
registered real provider (``dun_bradstreet``, ``clearbit``) plus its ``api_key``.
"""

from __future__ import annotations

from app.config import settings
from app.services.enrichment_adapters.base import VendorEnrichmentAdapter

_REGISTRY: dict[str, type[VendorEnrichmentAdapter]] = {}


def register_enrichment_adapter(provider: str):
    """Decorator that registers a vendor-enrichment adapter under a provider name."""

    def wrapper(cls: type[VendorEnrichmentAdapter]):
        _REGISTRY[provider] = cls
        return cls

    return wrapper


def get_enrichment_adapter(enrichment_config: dict | None) -> VendorEnrichmentAdapter:
    """Resolve the configured external-enrichment provider.

    Config shape (read from ``Organization.settings.enrichment``):
        {
            "provider": "mock" | "dun_bradstreet" | "clearbit",
            "api_key": "...",          # required by the real providers
            ...provider-specific fields...
        }

    Resolution order: per-org ``provider`` → ``AP_VENDOR_ENRICHMENT_PROVIDER``
    env default (``mock``). An unknown provider name falls back to ``mock`` so a
    typo can never break enrichment — but the real providers still **fail closed**
    on a missing ``api_key`` at call time (no hardcoded fallback secret).
    """
    # Trigger registration of the built-in adapters.
    import app.services.enrichment_adapters.clearbit  # noqa: F401
    import app.services.enrichment_adapters.dun_bradstreet  # noqa: F401
    import app.services.enrichment_adapters.mock_adapter  # noqa: F401

    cfg = enrichment_config or {}
    provider = (cfg.get("provider") or settings.vendor_enrichment_provider or "mock").lower()
    cls = _REGISTRY.get(provider) or _REGISTRY["mock"]
    return cls(cfg)
