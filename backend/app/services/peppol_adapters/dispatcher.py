"""PEPPOL adapter dispatcher — selects the Access Point adapter from config."""

from __future__ import annotations

from app.config import settings
from app.services.peppol_adapters.base import PeppolAdapter

_ADAPTER_REGISTRY: dict[str, type[PeppolAdapter]] = {}


def register_peppol_adapter(provider: str):
    """Decorator to register a PEPPOL adapter class under a provider name."""

    def wrapper(cls: type[PeppolAdapter]):
        _ADAPTER_REGISTRY[provider] = cls
        return cls

    return wrapper


def get_peppol_adapter(peppol_config: dict | None) -> PeppolAdapter:
    """Build the adapter for the configured provider.

    Config shape (lives in ``Organization.settings.peppol``)::

        {
            "provider": "mock" | "as4_gateway",
            "gateway_url": "...",         # as4_gateway only
            "api_key": "...",             # as4_gateway only — sops in deployed
            "sender_scheme": "9930",      # our (C1) EAS scheme
            "sender_value": "DE...",      # our registered id
            "webhook_secret": "..."       # future inbound HMAC verify
        }

    Falls back to the in-process ``mock`` adapter when no config exists or the
    provider is unknown — keeps local dev painless (no PEPPOL credential) and
    prevents a missed config from 500-ing the send path.
    """
    config = peppol_config or {}
    provider = config.get("provider") or settings.peppol_provider  # default "mock"
    adapter_cls = _ADAPTER_REGISTRY.get(provider) or _ADAPTER_REGISTRY.get("mock")
    if adapter_cls is None:
        raise ValueError(f"No PEPPOL adapter registered for '{provider}' and no mock fallback")
    return adapter_cls(config)


def list_available_providers() -> list[str]:
    return sorted(_ADAPTER_REGISTRY.keys())
