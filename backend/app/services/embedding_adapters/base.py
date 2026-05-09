"""Base embedding adapter interface.

Embeddings turn invoice text into fixed-length vectors for nearest-neighbor
retrieval. Each adapter must produce vectors of the same dimensionality so
they can be compared in the shared `invoice_embeddings` table.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class EmbeddingResult:
    vector: list[float]
    model: str
    token_count: int = 0


class EmbeddingAdapter:
    provider_name: str = "base"
    # Adapters must agree on dimension so stored vectors remain comparable.
    dimensions: int = 1536

    def __init__(self, config: dict):
        self.config = config

    async def embed(self, text: str) -> EmbeddingResult:
        raise NotImplementedError

    async def test_connection(self) -> bool:
        raise NotImplementedError
