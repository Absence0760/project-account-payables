"""QMS adapter dispatcher — picks the provider from org config."""

from __future__ import annotations

from app.services.qms_adapters.base import QMSAdapter

_REGISTRY: dict[str, type[QMSAdapter]] = {}


def register_qms_adapter(provider: str):
    """Decorator that registers a QMS adapter under a provider name."""

    def wrapper(cls: type[QMSAdapter]):
        _REGISTRY[provider] = cls
        return cls

    return wrapper


def get_qms_adapter(qms_config: dict | None) -> QMSAdapter:
    """Resolve the configured QMS provider.

    Config shape (read from ``Organization.settings.qms``):
        {
            "provider": "mock" | "generic" | ...,
            "base_url": "...",
            "api_key": "...",
            ...provider-specific fields...
        }

    Falls back to ``mock`` on empty config or an unknown provider. The mock
    is safe in local dev (deterministic records, no network, no credential)
    but unsafe in prod — deployments must set ``provider`` explicitly. Real
    providers (``generic``) fail closed without a ``base_url`` + ``api_key``
    rather than silently inventing inspection rows.
    """
    # Trigger registration of the built-in adapters.
    import app.services.qms_adapters.generic_qms  # noqa: F401
    import app.services.qms_adapters.mock_adapter  # noqa: F401

    cfg = qms_config or {}
    provider = (cfg.get("provider") or "mock").lower()
    cls = _REGISTRY.get(provider) or _REGISTRY["mock"]
    return cls(cfg)
