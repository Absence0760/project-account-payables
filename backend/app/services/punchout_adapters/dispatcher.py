"""Punch-out adapter dispatcher — selects the adapter from config.

Mirrors ``peppol_adapters/dispatcher.py``: a registry decorator
(``@register_punchout_adapter``) + a ``get_punchout_adapter`` that picks the
provider from the per-org ``Organization.settings.punchout`` block, falling
back to the process-level ``AP_PUNCHOUT_PROVIDER`` (default ``mock``), and to
``mock`` when the provider is unknown — so a missed config never 500s the start
path and local dev needs no supplier credential.
"""

from __future__ import annotations

from app.config import settings
from app.services.punchout_adapters.base import PunchoutAdapter

_ADAPTER_REGISTRY: dict[str, type[PunchoutAdapter]] = {}


def register_punchout_adapter(provider: str):
    """Decorator to register a punch-out adapter class under a provider name."""

    def wrapper(cls: type[PunchoutAdapter]):
        _ADAPTER_REGISTRY[provider] = cls
        return cls

    return wrapper


def get_punchout_adapter(punchout_config: dict | None) -> PunchoutAdapter:
    """Build the adapter for the configured provider.

    Config shape (lives in ``Organization.settings.punchout``)::

        {
            "provider": "mock" | "cxml",
            "shared_secret": "...",      # cxml only — sops in deployed; no fallback
            "buyer_identity": "...",     # our network/org id in the cXML header
            "protocol": "cxml" | "oci"   # cxml adapter supports both shapes
        }

    Falls back to the in-process ``mock`` adapter when no config exists or the
    provider is unknown.
    """
    config = punchout_config or {}
    provider = config.get("provider") or settings.punchout_provider  # default "mock"
    adapter_cls = _ADAPTER_REGISTRY.get(provider) or _ADAPTER_REGISTRY.get("mock")
    if adapter_cls is None:
        raise ValueError(f"No punch-out adapter registered for '{provider}' and no mock fallback")
    return adapter_cls(config)


def list_available_providers() -> list[str]:
    return sorted(_ADAPTER_REGISTRY.keys())
