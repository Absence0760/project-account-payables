"""Card webhook body-size cap (memory-exhaustion DoS on a public route).

`card_webhook` used to `await request.body()` with no size cap at all — the
HMAC check can't run until the owning tenant/card is identified from the
parsed body, so an unauthenticated attacker could POST an arbitrarily large
payload and have it buffered fully into memory before anything ever rejected
it. The guard bounds the body in two phases, mirroring
`erp_webhook`/`peppol_inbound`/`payment_webhook`: reject on a declared
Content-Length over the cap BEFORE reading the body at all, then re-check the
actual read length in case the header lied or was absent (e.g. chunked
transfer).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


def _fake_request(body: bytes, headers: dict | None = None):
    req = MagicMock()
    req.body = AsyncMock(return_value=body)
    req.headers = headers or {}
    return req


@pytest.mark.asyncio
async def test_content_length_over_cap_rejects_before_body_read(monkeypatch):
    """A declared Content-Length over the cap must reject WITHOUT ever
    awaiting `request.body()` — the whole point is bounding memory before
    anything is buffered."""
    from app.api.cards import card_webhook
    from app.config import settings

    monkeypatch.setattr(settings, "card_webhook_max_bytes", 1024)
    request = _fake_request(b"", {"content-length": "999999"})

    result = await card_webhook(provider="lithic", request=request)

    assert result is None  # silent 204, not a raised exception
    request.body.assert_not_awaited()


@pytest.mark.asyncio
async def test_content_length_malformed_rejects_before_body_read(monkeypatch):
    """A non-integer Content-Length header must also reject before reading —
    a malformed header shouldn't fall through to an unbounded read."""
    from app.api.cards import card_webhook
    from app.config import settings

    monkeypatch.setattr(settings, "card_webhook_max_bytes", 1024)
    request = _fake_request(b"", {"content-length": "not-a-number"})

    result = await card_webhook(provider="lithic", request=request)

    assert result is None
    request.body.assert_not_awaited()


@pytest.mark.asyncio
async def test_oversized_body_without_content_length_rejects_after_read(monkeypatch):
    """Simulates chunked transfer (no Content-Length header): the body is
    read once, then rejected by the post-read length check — before the
    handler ever calls `request.json()`."""
    from app.api.cards import card_webhook
    from app.config import settings

    monkeypatch.setattr(settings, "card_webhook_max_bytes", 1024)
    big_body = b"x" * 2048
    request = _fake_request(big_body, {})
    request.json = AsyncMock(side_effect=AssertionError("must not parse an oversized body"))

    result = await card_webhook(provider="lithic", request=request)

    assert result is None
    request.body.assert_awaited_once()
    request.json.assert_not_awaited()


@pytest.mark.asyncio
async def test_content_length_understates_actual_size_still_rejects(monkeypatch):
    """A Content-Length header that lies (understates the real body) must
    still be caught by the post-read re-check, not trusted blindly."""
    from app.api.cards import card_webhook
    from app.config import settings

    monkeypatch.setattr(settings, "card_webhook_max_bytes", 1024)
    big_body = b"x" * 2048
    request = _fake_request(big_body, {"content-length": "10"})
    request.json = AsyncMock(side_effect=AssertionError("must not parse an oversized body"))

    result = await card_webhook(provider="lithic", request=request)

    assert result is None
    request.body.assert_awaited_once()
    request.json.assert_not_awaited()
