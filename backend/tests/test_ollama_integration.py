"""Live integration test for the ollama extraction adapter against the container.

Verifies the adapter can reach the Ollama Compose container and that its
model-presence check (test_connection) works end to end.

Gated + local-only: skipped unless an Ollama server is reachable AND has at least
one model pulled. NOT wired into CI — model inference is too heavy for the CI
runners (the plan excludes Ollama from the CI service set), so this runs on a dev
box after:

    pnpm ollama:up
    pnpm ollama:pull qwen2.5:0.5b        # any model; vision model for real extraction

Probes FEOH_OLLAMA_BASE_URL if set, else the container port (11435), then the
native default (11434).
"""

from __future__ import annotations

import os

import httpx
import pytest

_CANDIDATES = [
    os.environ.get("FEOH_OLLAMA_BASE_URL", ""),
    "http://localhost:11435",  # Compose container (pnpm ollama:up)
    "http://localhost:11434",  # native install
]


def _resolve() -> tuple[str, str] | None:
    """Return (base_url, model_name) for the first reachable Ollama with a model."""
    for base in [c for c in _CANDIDATES if c]:
        try:
            resp = httpx.get(f"{base}/api/tags", timeout=2.0)
        except httpx.HTTPError:
            continue
        if resp.status_code != 200:
            continue
        models = [m["name"] for m in resp.json().get("models", [])]
        if models:
            return base, models[0]
    return None


_RESOLVED = _resolve()

pytestmark = [
    pytest.mark.skipif(
        _RESOLVED is None,
        reason="Ollama not reachable with a pulled model — `pnpm ollama:up` + `pnpm ollama:pull`",
    ),
    pytest.mark.asyncio,
]


async def test_adapter_resolves_container_base_url(monkeypatch):
    base, _model = _RESOLVED
    from app.config import settings

    monkeypatch.setattr(settings, "ollama_base_url", base)
    from app.services.extraction_adapters.ollama import OllamaAdapter

    # Empty per-org config falls back to the global base URL.
    assert OllamaAdapter({})._base_url() == base


async def test_test_connection_true_for_a_present_model():
    base, model = _RESOLVED
    from app.services.extraction_adapters.ollama import OllamaAdapter

    adapter = OllamaAdapter({"base_url": base, "model": model})
    assert await adapter.test_connection() is True


async def test_test_connection_false_for_absent_model():
    base, _model = _RESOLVED
    from app.services.extraction_adapters.ollama import OllamaAdapter

    adapter = OllamaAdapter({"base_url": base, "model": "definitely-not-a-real-model:0b"})
    assert await adapter.test_connection() is False
