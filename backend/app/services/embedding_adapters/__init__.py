"""Embedding adapters — text-to-vector providers for RAG retrieval."""

from app.services.embedding_adapters import mock_adapter as _mock  # noqa: F401
from app.services.embedding_adapters import openai_adapter as _openai  # noqa: F401
from app.services.embedding_adapters.base import EmbeddingAdapter, EmbeddingResult
from app.services.embedding_adapters.dispatcher import (
    get_embedding_adapter,
    list_available_providers,
    register_embedding_adapter,
)

__all__ = [
    "EmbeddingAdapter",
    "EmbeddingResult",
    "get_embedding_adapter",
    "list_available_providers",
    "register_embedding_adapter",
]
