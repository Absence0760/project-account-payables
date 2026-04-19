"""Embedding adapter dispatcher."""

from __future__ import annotations

from app.config import settings
from app.services.embedding_adapters.base import EmbeddingAdapter

_ADAPTER_REGISTRY: dict[str, type[EmbeddingAdapter]] = {}


def register_embedding_adapter(provider: str):
    def wrapper(cls: type[EmbeddingAdapter]):
        _ADAPTER_REGISTRY[provider] = cls
        return cls

    return wrapper


def get_embedding_adapter() -> EmbeddingAdapter:
    provider = settings.embedding_provider or "mock"

    adapter_cls = _ADAPTER_REGISTRY.get(provider)
    if adapter_cls is None:
        adapter_cls = _ADAPTER_REGISTRY.get("mock")
        if adapter_cls is None:
            raise ValueError(f"No embedding adapter registered for '{provider}'")

    return adapter_cls(
        {
            "api_key": settings.embedding_api_key,
            "model": settings.embedding_model,
            "dimensions": settings.embedding_dimensions,
        }
    )


def list_available_providers() -> list[str]:
    return sorted(_ADAPTER_REGISTRY.keys())
