"""Chat-notification adapter dispatcher — selects the provider per call.

Mirrors `email_adapters/dispatcher.py`: a decorator registry plus a factory
that resolves the provider from the **per-org** chat-notification settings
(falling back to the platform `FEOH_CHAT_NOTIFICATION_PROVIDER` default `mock`).
An unknown provider key falls back to `mock` so a bad config can never raise
into the notification chokepoint.
"""

from __future__ import annotations

from app.config import settings
from app.services.chat_notification_adapters.base import ChatNotificationAdapter

_ADAPTER_REGISTRY: dict[str, type[ChatNotificationAdapter]] = {}


def register_chat_notification_adapter(provider: str):
    """Decorator to register a chat-notification adapter class."""

    def wrapper(cls: type[ChatNotificationAdapter]):
        _ADAPTER_REGISTRY[provider] = cls
        return cls

    return wrapper


def get_chat_notification_adapter(org_config: dict | None = None) -> ChatNotificationAdapter:
    """Build the configured chat-notification adapter.

    ``org_config`` is the org's ``settings.chat_notifications`` dict (provider +
    webhook_url + events). When it omits a provider, the platform default
    (`FEOH_CHAT_NOTIFICATION_PROVIDER`, default `mock`) is used. An unknown
    provider falls back to `mock` — never raises.
    """
    org_config = org_config or {}
    provider = org_config.get("provider") or settings.chat_notification_provider or "mock"

    adapter_cls = _ADAPTER_REGISTRY.get(provider)
    if adapter_cls is None:
        adapter_cls = _ADAPTER_REGISTRY.get("mock")
        if adapter_cls is None:  # pragma: no cover — mock is always registered
            raise ValueError(f"No chat-notification adapter registered for '{provider}'")

    return adapter_cls(org_config)


def list_available_providers() -> list[str]:
    return sorted(_ADAPTER_REGISTRY.keys())
