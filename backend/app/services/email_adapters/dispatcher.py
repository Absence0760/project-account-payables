"""Email adapter dispatcher — selects the right provider based on app settings."""

from __future__ import annotations

from app.config import settings
from app.services.email_adapters.base import EmailAdapter

_ADAPTER_REGISTRY: dict[str, type[EmailAdapter]] = {}


def register_email_adapter(provider: str):
    """Decorator to register an email adapter class."""

    def wrapper(cls: type[EmailAdapter]):
        _ADAPTER_REGISTRY[provider] = cls
        return cls

    return wrapper


def get_email_adapter() -> EmailAdapter:
    """Build the configured email adapter. Falls back to console for local dev."""
    provider = settings.email_provider or "console"

    adapter_cls = _ADAPTER_REGISTRY.get(provider)
    if adapter_cls is None:
        adapter_cls = _ADAPTER_REGISTRY.get("console")
        if adapter_cls is None:
            raise ValueError(f"No email adapter registered for '{provider}'")

    config = {
        "from_address": settings.email_from or "no-reply@example.com",
        "region": settings.aws_ses_region,
    }
    return adapter_cls(config)


def list_available_providers() -> list[str]:
    return sorted(_ADAPTER_REGISTRY.keys())
