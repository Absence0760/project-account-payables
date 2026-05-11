"""Sanctions adapter dispatcher."""

from __future__ import annotations

from app.services.sanctions_adapters.base import SanctionsAdapter

_REGISTRY: dict[str, type[SanctionsAdapter]] = {}


def register_sanctions_adapter(provider: str):
    """Decorator that registers a sanctions adapter under a provider name."""

    def wrapper(cls: type[SanctionsAdapter]):
        _REGISTRY[provider] = cls
        return cls

    return wrapper


def get_sanctions_adapter(compliance_config: dict | None) -> SanctionsAdapter:
    """Resolve the configured sanctions provider.

    Config shape (read from `Organization.settings.compliance.sanctions`):
        {
            "provider": "mock" | "complyadvantage" | ...,
            "api_key": "...",
            ...provider-specific fields...
        }

    Falls back to `mock` on empty config or unknown provider. The mock
    is safe in local dev (always returns 'clear') but unsafe in prod —
    deployments must set `provider` explicitly. The compliance service
    surfaces a warning in its result so this misconfiguration is
    visible to the AP team rather than silently allowing every
    payment.
    """
    # Trigger registration of the built-in adapters.
    import app.services.sanctions_adapters.complyadvantage  # noqa: F401
    import app.services.sanctions_adapters.mock_adapter  # noqa: F401

    cfg = compliance_config or {}
    provider = (cfg.get("provider") or "mock").lower()
    cls = _REGISTRY.get(provider) or _REGISTRY["mock"]
    return cls(cfg)
