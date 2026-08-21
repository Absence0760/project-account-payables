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

# How much of a configured provider name may be echoed back in an error. Bounded
# so an absurd settings value can't bloat a log line or a response body.
_PROVIDER_NAME_ECHO_LIMIT = 50


class UnknownEnrichmentProviderError(ValueError):
    """``settings.enrichment.provider`` names a source we have no adapter for.

    Raised instead of silently substituting ``mock``, which returns *synthetic*
    firmographics — a deterministic fabricated legal name, registered address,
    DUNS number and employee count — with ``matched=True``. A typo in the
    provider name therefore produced a confident, plausible, entirely invented
    identity for a real supplier, presented to a steward as a D&B / Clearbit
    lookup and one click away from being written onto the vendor row by
    ``POST /api/enrichment/vendors/{id}/apply`` (`name` is a *screened* identity
    field — the apply path re-runs sanctions screening against it).

    Same call as ``decisions.md`` §29 (payments / ERP / FX) and §36 (sanctions):
    the fixture adapter is never inert, so substituting it converts a
    configuration error into a silent wrong answer. An absent or empty provider
    still resolves to ``mock`` — the local-first default.
    """

    def __init__(self, provider: str):
        self.provider = str(provider)[:_PROVIDER_NAME_ECHO_LIMIT]
        super().__init__(
            f"No vendor-enrichment adapter registered for provider '{self.provider}'. "
            f"Registered providers: {', '.join(list_available_providers())}."
        )


def register_enrichment_adapter(provider: str):
    """Decorator that registers a vendor-enrichment adapter under a provider name."""

    def wrapper(cls: type[VendorEnrichmentAdapter]):
        _REGISTRY[provider] = cls
        return cls

    return wrapper


def _load_builtin_adapters() -> None:
    """Trigger registration of the built-in adapters."""
    import app.services.enrichment_adapters.clearbit  # noqa: F401
    import app.services.enrichment_adapters.dun_bradstreet  # noqa: F401
    import app.services.enrichment_adapters.mock_adapter  # noqa: F401


def get_enrichment_adapter(enrichment_config: dict | None) -> VendorEnrichmentAdapter:
    """Resolve the configured external-enrichment provider.

    Config shape (read from ``Organization.settings.enrichment``):
        {
            "provider": "mock" | "dun_bradstreet" | "clearbit",
            "api_key": "...",          # required by the real providers
            ...provider-specific fields...
        }

    Resolution order: per-org ``provider`` → ``FEOH_VENDOR_ENRICHMENT_PROVIDER``
    env default → ``mock``. **A named provider with no registered adapter raises
    :class:`UnknownEnrichmentProviderError`** rather than falling back to
    ``mock``, whose synthetic firmographics would otherwise be indistinguishable
    from a real lookup (see the exception's docstring). The real providers still
    **fail closed** on a missing ``api_key`` at call time (no hardcoded fallback
    secret).
    """
    _load_builtin_adapters()

    cfg = enrichment_config or {}
    provider = (cfg.get("provider") or settings.vendor_enrichment_provider or "mock").lower()
    cls = _REGISTRY.get(provider)
    if cls is None:
        raise UnknownEnrichmentProviderError(provider)
    return cls(cfg)


def list_available_providers() -> list[str]:
    _load_builtin_adapters()
    return sorted(_REGISTRY.keys())
