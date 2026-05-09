"""OpenAI embedding adapter — text-embedding-3-small by default.

Uses the raw /v1/embeddings endpoint via httpx to avoid adding the openai
SDK as a hard dependency (we only need two API calls: embed + list-models).
"""

from __future__ import annotations

import httpx

from app.services.embedding_adapters.base import EmbeddingAdapter, EmbeddingResult
from app.services.embedding_adapters.dispatcher import register_embedding_adapter

OPENAI_EMBEDDINGS_URL = "https://api.openai.com/v1/embeddings"


@register_embedding_adapter("openai")
class OpenAIEmbeddingAdapter(EmbeddingAdapter):
    provider_name = "openai"

    def __init__(self, config: dict):
        super().__init__(config)
        self.dimensions = int(config.get("dimensions", 1536))

    async def embed(self, text: str) -> EmbeddingResult:
        api_key = self.config.get("api_key") or ""
        model = self.config.get("model") or "text-embedding-3-small"

        if not api_key:
            raise RuntimeError("AP_EMBEDDING_API_KEY is not set")

        # Trim to OpenAI's ~8k token safe ceiling — character-level approx
        # so we don't need a tokenizer dep. 4 chars/token is a safe upper bound.
        trimmed = text[:32000]

        body = {
            "model": model,
            "input": trimmed,
        }
        # text-embedding-3-small supports explicit dimensions from 512-1536.
        if self.dimensions and self.dimensions < 1536:
            body["dimensions"] = self.dimensions

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                OPENAI_EMBEDDINGS_URL,
                json=body,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            )
        resp.raise_for_status()
        data = resp.json()

        vector = data["data"][0]["embedding"]
        usage = data.get("usage") or {}

        return EmbeddingResult(
            vector=vector,
            model=model,
            token_count=usage.get("prompt_tokens", 0),
        )

    async def test_connection(self) -> bool:
        try:
            await self.embed("ping")
            return True
        except Exception:
            return False
