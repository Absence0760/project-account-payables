"""Assistant adapter registry + selector.

Mirrors ``services/extraction_adapters/dispatcher.py``. Local-first guarantee:
if ``claude`` is selected but no API key is resolvable, auto-downgrade to
``mock`` so a fresh clone runs with no credential.
"""

from __future__ import annotations

from app.services.assistant.base import AssistantAdapter

_ADAPTER_REGISTRY: dict[str, type[AssistantAdapter]] = {}


def register_assistant_adapter(provider: str):
    """Decorator to register an assistant adapter class."""

    def wrapper(cls: type[AssistantAdapter]):
        _ADAPTER_REGISTRY[provider] = cls
        return cls

    return wrapper


def get_assistant_adapter(config: dict) -> AssistantAdapter:
    """Build the adapter for ``config['provider']``.

    Config shape::

        {"provider": "mock"|"claude", "api_key": str, "model": str}

    Auto-downgrade ``claude`` → ``mock`` when no key is configured — the
    local-first rail.
    """
    provider = config.get("provider") or "mock"
    if provider == "claude" and not config.get("api_key"):
        provider = "mock"
    cls = _ADAPTER_REGISTRY.get(provider) or _ADAPTER_REGISTRY["mock"]
    return cls(config)


def list_available_providers() -> list[str]:
    return sorted(_ADAPTER_REGISTRY.keys())


# Register adapters on import (decorators run on module load).
from app.services.assistant import claude_adapter, mock_adapter  # noqa: E402,F401
