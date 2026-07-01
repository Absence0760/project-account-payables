"""Adapter-level idempotent cancel: an already-closed card is SUCCESS.

`cancel_card` must treat "card already closed/terminated at the provider" as a
confirmed cancel (return True), not a failed one. This cleanly resolves the
retry case where a first cancel closed the card at the provider but the DB write
failed and AP retries — the second attempt should confirm, not error out.

Covered per real adapter (lithic, nium):
  - provider 404 (card gone)            → True
  - provider 409 (state conflict)       → True
  - 200 echoing an already-CLOSED state → True
  - other error, live status terminal   → True (confirmed via status recheck)
  - other error, live status active     → False (fail-safe: not confirmed)
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload
        self.text = ""

    def json(self):
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload


class _FakeClient:
    """Async-context-manager stand-in for httpx.AsyncClient. Routes each HTTP
    verb to a preset response so we can exercise the adapter's branching without
    a network call."""

    def __init__(self, responses: dict[str, _FakeResponse]):
        self._responses = responses

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def patch(self, *a, **k):
        return self._responses["patch"]

    async def post(self, *a, **k):
        return self._responses["post"]

    async def get(self, *a, **k):
        return self._responses["get"]


def _client_factory(responses: dict[str, _FakeResponse]):
    def _factory(*a, **k):
        return _FakeClient(responses)

    return _factory


# ---------------------------------------------------------------- Lithic ----


def _lithic():
    from app.services.card_adapters.lithic import LithicAdapter

    return LithicAdapter({"api_key": "test", "sandbox": True})


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [404, 409])
async def test_lithic_already_closed_status_is_success(code):
    adapter = _lithic()
    responses = {
        "patch": _FakeResponse(code),
        "get": _FakeResponse(200),
        "post": _FakeResponse(200),
    }
    with patch("app.services.card_adapters.lithic.httpx.AsyncClient", _client_factory(responses)):
        assert await adapter.cancel_card("card_tok") is True


@pytest.mark.asyncio
async def test_lithic_200_echoing_closed_state_is_success():
    adapter = _lithic()
    responses = {"patch": _FakeResponse(200, {"state": "CLOSED"})}
    with patch("app.services.card_adapters.lithic.httpx.AsyncClient", _client_factory(responses)):
        assert await adapter.cancel_card("card_tok") is True


@pytest.mark.asyncio
async def test_lithic_200_no_body_is_success():
    adapter = _lithic()
    responses = {"patch": _FakeResponse(200)}
    with patch("app.services.card_adapters.lithic.httpx.AsyncClient", _client_factory(responses)):
        assert await adapter.cancel_card("card_tok") is True


@pytest.mark.asyncio
async def test_lithic_other_error_confirms_via_status_closed():
    adapter = _lithic()
    # PATCH errors (500), but the live GET shows the card already CLOSED.
    responses = {
        "patch": _FakeResponse(500),
        "get": _FakeResponse(200, {"state": "CLOSED"}),
    }
    with patch("app.services.card_adapters.lithic.httpx.AsyncClient", _client_factory(responses)):
        assert await adapter.cancel_card("card_tok") is True


@pytest.mark.asyncio
async def test_lithic_other_error_active_stays_false():
    adapter = _lithic()
    # PATCH errors and the card is still OPEN/active → NOT confirmed → False.
    responses = {
        "patch": _FakeResponse(500),
        "get": _FakeResponse(200, {"state": "OPEN"}),
    }
    with patch("app.services.card_adapters.lithic.httpx.AsyncClient", _client_factory(responses)):
        assert await adapter.cancel_card("card_tok") is False


# ------------------------------------------------------------------ Nium ----


def _nium():
    from app.services.card_adapters.nium import NiumAdapter

    adapter = NiumAdapter(
        {
            "client_id": "id",
            "client_secret": "secret",
            "customer_hash_id": "cust",
            "wallet_hash_id": "wallet",
            "sandbox": True,
        }
    )
    # Skip the real token fetch — the auth POST is irrelevant to cancel logic.
    adapter._headers = AsyncMock(return_value={"Authorization": "Bearer x"})
    return adapter


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [404, 409])
async def test_nium_already_blocked_status_is_success(code):
    adapter = _nium()
    responses = {"post": _FakeResponse(code), "get": _FakeResponse(200)}
    with patch("app.services.card_adapters.nium.httpx.AsyncClient", _client_factory(responses)):
        assert await adapter.cancel_card("card_tok") is True


@pytest.mark.asyncio
async def test_nium_200_is_success():
    adapter = _nium()
    responses = {"post": _FakeResponse(200)}
    with patch("app.services.card_adapters.nium.httpx.AsyncClient", _client_factory(responses)):
        assert await adapter.cancel_card("card_tok") is True


@pytest.mark.asyncio
async def test_nium_other_error_confirms_via_status_blocked():
    adapter = _nium()
    responses = {
        "post": _FakeResponse(500),
        "get": _FakeResponse(200, {"cardStatus": "BLOCKED"}),
    }
    with patch("app.services.card_adapters.nium.httpx.AsyncClient", _client_factory(responses)):
        assert await adapter.cancel_card("card_tok") is True


@pytest.mark.asyncio
async def test_nium_other_error_active_stays_false():
    adapter = _nium()
    responses = {
        "post": _FakeResponse(500),
        "get": _FakeResponse(200, {"cardStatus": "ACTIVE"}),
    }
    with patch("app.services.card_adapters.nium.httpx.AsyncClient", _client_factory(responses)):
        assert await adapter.cancel_card("card_tok") is False


# ------------------------------------------------------------------ Mock ----


@pytest.mark.asyncio
async def test_mock_cancel_is_idempotent_success():
    from app.services.card_adapters.mock_adapter import MockCardAdapter

    adapter = MockCardAdapter({})
    card = SimpleNamespace(provider_card_id="mock_card_1")
    # Repeated cancels of the same card are both success (idempotent).
    assert await adapter.cancel_card(card.provider_card_id) is True
    assert await adapter.cancel_card(card.provider_card_id) is True
