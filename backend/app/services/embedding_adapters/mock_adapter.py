"""Mock embedding adapter — deterministic hash-to-vector for local dev.

Same text → same vector → cosine similarity 1.0. Different text → different
vectors with plausible distances. Enough to end-to-end test the RAG flow
without an OpenAI key, but not semantically meaningful.
"""

from __future__ import annotations

import hashlib
import math
import random

from app.services.embedding_adapters.base import EmbeddingAdapter, EmbeddingResult
from app.services.embedding_adapters.dispatcher import register_embedding_adapter


@register_embedding_adapter("mock")
class MockEmbeddingAdapter(EmbeddingAdapter):
    provider_name = "mock"

    def __init__(self, config: dict):
        super().__init__(config)
        self.dimensions = int(config.get("dimensions", 1536))

    async def embed(self, text: str) -> EmbeddingResult:
        # Seed a PRNG with a stable hash of the text so identical inputs
        # always produce identical vectors.
        seed = int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")
        rng = random.Random(seed)
        vec = [rng.gauss(0.0, 1.0) for _ in range(self.dimensions)]

        # Normalize to unit length so cosine similarity === dot product.
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        vec = [v / norm for v in vec]

        return EmbeddingResult(
            vector=vec,
            model="mock",
            token_count=len(text.split()),
        )

    async def test_connection(self) -> bool:
        return True
