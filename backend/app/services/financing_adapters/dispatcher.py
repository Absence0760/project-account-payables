"""Supplier-financing adapter dispatcher — picks the provider from org config."""

from __future__ import annotations

from app.services.financing_adapters.base import FinancingAdapter

_REGISTRY: dict[str, type[FinancingAdapter]] = {}


def register_financing_adapter(provider: str):
    """Decorator that registers a financing adapter under a provider name."""

    def wrapper(cls: type[FinancingAdapter]):
        _REGISTRY[provider] = cls
        return cls

    return wrapper


def get_financing_adapter(financing_config: dict | None) -> FinancingAdapter:
    """Resolve the configured supplier-financing provider.

    Config shape (read from `Organization.settings.financing`):
        {
            "provider": "mock" | "c2fo" | ...,
            "api_key": "...",
            ...provider-specific fields...
        }

    Falls back to `mock` on empty config or unknown provider. The mock
    is safe in local dev (deterministic quotes, no network, no
    credential) but unsafe in prod — deployments must set `provider`
    explicitly. Real providers (`c2fo`) fail closed without an
    `api_key` rather than silently quoting a fake offer.
    """
    # Trigger registration of the built-in adapters.
    import app.services.financing_adapters.c2fo  # noqa: F401
    import app.services.financing_adapters.mock_adapter  # noqa: F401

    cfg = financing_config or {}
    provider = (cfg.get("provider") or "mock").lower()
    cls = _REGISTRY.get(provider) or _REGISTRY["mock"]
    return cls(cfg)
