"""Extraction adapter dispatcher — picks the right provider based on org config."""

from __future__ import annotations

from app.services.extraction_adapters.base import ExtractionAdapter

_ADAPTER_REGISTRY: dict[str, type[ExtractionAdapter]] = {}


def register_extraction_adapter(provider: str):
    """Decorator to register an extraction adapter class."""

    def wrapper(cls: type[ExtractionAdapter]):
        _ADAPTER_REGISTRY[provider] = cls
        return cls

    return wrapper


def get_extraction_adapter(extraction_config: dict) -> ExtractionAdapter:
    """Create the appropriate adapter based on org extraction config.

    Config shape:
        {
            "program_type": "platform" | "byok",
            "provider": "claude_vision" | "openai_vision" | "aws_textract" | "mock",
            ...provider-specific fields...
        }

    If program_type is "platform", platform-level keys from app settings are used.
    If "byok", customer-provided keys from org settings are used.
    """
    provider = extraction_config.get("provider", "mock")

    adapter_cls = _ADAPTER_REGISTRY.get(provider)
    if not adapter_cls:
        adapter_cls = _ADAPTER_REGISTRY.get("mock")
        if not adapter_cls:
            raise ValueError(f"No extraction adapter registered for '{provider}'")

    return adapter_cls(extraction_config)


def list_available_providers() -> list[str]:
    return sorted(_ADAPTER_REGISTRY.keys())
