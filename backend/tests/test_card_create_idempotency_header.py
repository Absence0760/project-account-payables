"""Adapter-level: card creation carries the provider's idempotency key.

A virtual card is spendable money. If `httpx` times out AFTER Lithic/Nium have
already provisioned the card, we hold no `provider_card_id` — the live card is
orphaned and ungoverned — and an unkeyed retry mints a SECOND one. The DB index
`uq_virtual_cards_one_live_per_invoice` cannot help: neither card ever reached
our database.

The fix is the provider's own idempotency channel, and the two providers do NOT
share a convention:

  - Lithic  → `Idempotency-Key` header on `POST /v1/cards` (must be a UUID;
              keys retained 30 days).
  - Nium    → `x-request-id` header on the POST (keys purged after 24 hours).

These pin the exact header per provider so a future refactor can't quietly drop
the key or send Lithic's header to Nium.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest

from app.services.card_adapters.base import VirtualCardPayload

IDEMPOTENCY_KEY = "2f8c1f3e-4a6b-4c2d-9e1f-7b3a5c9d0e11"


def _payload(**overrides) -> VirtualCardPayload:
    kwargs = {
        "correlation_id": "corr-1",
        "invoice_id": "inv-1",
        "vendor_name": "Acme Supplies",
        "vendor_email": None,
        "amount": Decimal("1234.56"),
        "currency": "USD",
        "idempotency_key": IDEMPOTENCY_KEY,
    }
    kwargs.update(overrides)
    return VirtualCardPayload(**kwargs)


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.text = ""

    def json(self):
        return self._payload


class _CapturingClient:
    """Async-context-manager stand-in for httpx.AsyncClient that records the
    headers of every POST instead of making a network call."""

    def __init__(self, sink: dict, body: dict):
        self._sink = sink
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, *a, **k):
        self._sink.setdefault("posts", []).append({"url": url, "headers": k.get("headers") or {}})
        return _FakeResponse(200, self._body)


def _client_factory(sink: dict, body: dict):
    def _factory(*a, **k):
        return _CapturingClient(sink, body)

    return _factory


# ---------------------------------------------------------------- Lithic ----


@pytest.mark.asyncio
async def test_lithic_create_card_sends_idempotency_key_header():
    from app.services.card_adapters.lithic import LithicAdapter

    adapter = LithicAdapter({"api_key": "test", "sandbox": True})
    sink: dict = {}
    body = {"token": "card_tok_1", "last_four": "4242"}

    with patch("app.services.card_adapters.lithic.httpx.AsyncClient", _client_factory(sink, body)):
        result = await adapter.create_card(_payload())

    assert result.success is True
    headers = sink["posts"][0]["headers"]
    assert headers["Idempotency-Key"] == IDEMPOTENCY_KEY


@pytest.mark.asyncio
async def test_lithic_omits_the_header_when_no_key_supplied():
    """No key is better than an unstable one: a fresh key per attempt would
    give false confidence while still double-issuing."""
    from app.services.card_adapters.lithic import LithicAdapter

    adapter = LithicAdapter({"api_key": "test", "sandbox": True})
    sink: dict = {}

    with patch(
        "app.services.card_adapters.lithic.httpx.AsyncClient",
        _client_factory(sink, {"token": "card_tok_1", "last_four": "4242"}),
    ):
        await adapter.create_card(_payload(idempotency_key=None))

    assert "Idempotency-Key" not in sink["posts"][0]["headers"]


# ------------------------------------------------------------------ Nium ----


@pytest.mark.asyncio
async def test_nium_create_card_sends_x_request_id_header():
    from app.services.card_adapters.nium import NiumAdapter

    adapter = NiumAdapter(
        {
            "client_id": "c",
            "client_secret": "s",
            "customer_hash_id": "cust",
            "wallet_hash_id": "wallet",
            "sandbox": True,
        }
    )
    adapter._access_token = "tok"  # skip the auth round-trip
    sink: dict = {}
    body = {"cardHashId": "nium_card_1", "maskedCardNumber": "************4242"}

    with patch("app.services.card_adapters.nium.httpx.AsyncClient", _client_factory(sink, body)):
        result = await adapter.create_card(_payload())

    assert result.success is True
    headers = sink["posts"][0]["headers"]
    # Nium's channel is x-request-id — NOT Lithic's Idempotency-Key.
    assert headers["x-request-id"] == IDEMPOTENCY_KEY


@pytest.mark.asyncio
async def test_nium_omits_the_header_when_no_key_supplied():
    from app.services.card_adapters.nium import NiumAdapter

    adapter = NiumAdapter(
        {
            "client_id": "c",
            "client_secret": "s",
            "customer_hash_id": "cust",
            "wallet_hash_id": "wallet",
            "sandbox": True,
        }
    )
    adapter._access_token = "tok"
    sink: dict = {}

    with patch(
        "app.services.card_adapters.nium.httpx.AsyncClient",
        _client_factory(sink, {"cardHashId": "nium_card_1", "maskedCardNumber": "****4242"}),
    ):
        await adapter.create_card(_payload(idempotency_key=None))

    assert "x-request-id" not in sink["posts"][0]["headers"]


# ------------------------------------------------------------------ mock ----


@pytest.mark.asyncio
async def test_mock_adapter_replays_the_same_card_for_a_repeated_key():
    """Local-first: the mock adapter models the provider guarantee, so the
    retry path is exercisable on a laptop with no provider account."""
    from app.services.card_adapters.mock_adapter import MockCardAdapter

    adapter = MockCardAdapter({})

    other_key = "9a1d0c77-1111-4222-8333-444455556666"

    first = await adapter.create_card(_payload())
    retry = await adapter.create_card(_payload())
    other = await adapter.create_card(_payload(idempotency_key=other_key))

    assert first.provider_card_id == retry.provider_card_id
    assert first.provider_card_id != other.provider_card_id
    assert (first.raw_response or {})["idempotency_key"] == IDEMPOTENCY_KEY


@pytest.mark.asyncio
async def test_mock_adapter_without_a_key_still_mints_unique_cards():
    from app.services.card_adapters.mock_adapter import MockCardAdapter

    adapter = MockCardAdapter({})

    a = await adapter.create_card(_payload(idempotency_key=None))
    b = await adapter.create_card(_payload(idempotency_key=None))

    assert a.provider_card_id != b.provider_card_id
