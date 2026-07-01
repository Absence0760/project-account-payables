"""Client-side idempotency for the Checkeeper check-printing adapter.

Checkeeper has NO native idempotency key, and a physical check is an
irreversible negotiable instrument — two checks mailed for one liability
is a double-payment recoverable only by the vendor's cooperation or legal
action. The adapter therefore claims a ``correlation_id``-keyed Redis
``SET NX`` slot right before POSTing ``/checks``. These tests pin that:

  - the first issue claims the slot and POSTs exactly one check;
  - a retry with the SAME correlation_id is suppressed (no second POST);
  - a Redis outage fails CLOSED (never issues without the guard);
  - a clean API rejection releases the slot so a legit retry can proceed;
  - a transport error LEAVES the slot claimed (ambiguous — may have printed).

The slot is claimed only after the cheap pre-validation (method / mailing
address), so a validation reject never burns a correlation_id.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services.payment_adapters.base import PaymentPayload
from app.services.payment_adapters.checkeeper import CheckeeperAdapter

_ADDR = {
    "mailing_address": {
        "street": "1 Acme St",
        "city": "Anywhere",
        "state": "CA",
        "postal": "94000",
    }
}


def _payload(correlation_id: str = "cor-idem-1"):
    return PaymentPayload(
        correlation_id=correlation_id,
        invoice_id="inv-1",
        invoice_number="INV-1",
        vendor_name="Test Vendor",
        amount=Decimal("100.00"),
        currency="USD",
        method="check",
        description="Test check",
        vendor_bank=_ADDR,
        metadata={"organization_id": "org-1"},
    )


class _FakeResponse:
    def __init__(self, body, status_code=200):
        self._body = body
        self.status_code = status_code

    def json(self):
        return self._body


def _client_yielding(responses, calls):
    it = iter(responses)

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kw):
            calls.append({"url": url, **kw})
            resp = next(it)
            if isinstance(resp, Exception):
                raise resp
            return resp

    return _Client


def _fake_redis():
    """An in-memory SET-NX Redis good enough for the gate: `set(nx=True)`
    returns True the first time a key is seen, None thereafter; `delete`
    frees it."""
    store: set[str] = set()
    r = MagicMock()

    async def _set(key, _val, nx=False, ex=None):
        if nx and key in store:
            return None
        store.add(key)
        return True

    async def _delete(key):
        store.discard(key)
        return 1

    r.set = AsyncMock(side_effect=_set)
    r.delete = AsyncMock(side_effect=_delete)
    r._store = store
    return r


@pytest.mark.asyncio
async def test_first_issue_claims_slot_and_posts_one_check():
    adapter = CheckeeperAdapter({"api_key": "k", "bank_account_id": "bnk"})
    calls: list = []
    client = _client_yielding([_FakeResponse({"id": "chk_1", "status": "queued"})], calls)
    r = _fake_redis()

    with (
        patch("app.services.payment_adapters.checkeeper.httpx.AsyncClient", client),
        patch("app.redis.get_redis", AsyncMock(return_value=r)),
    ):
        result = await adapter.create_payment(_payload())

    assert result.success is True
    assert result.provider_payment_id == "chk_1"
    assert len(calls) == 1  # exactly one physical check issued
    assert "checkeeper:payment:cor-idem-1" in r._store


@pytest.mark.asyncio
async def test_retry_same_correlation_id_is_suppressed_without_second_post():
    """The core guarantee: a second create_payment for the same attempt
    prints NO second check."""
    adapter = CheckeeperAdapter({"api_key": "k", "bank_account_id": "bnk"})
    r = _fake_redis()

    # First issue succeeds.
    calls1: list = []
    client1 = _client_yielding([_FakeResponse({"id": "chk_1", "status": "queued"})], calls1)
    with (
        patch("app.services.payment_adapters.checkeeper.httpx.AsyncClient", client1),
        patch("app.redis.get_redis", AsyncMock(return_value=r)),
    ):
        first = await adapter.create_payment(_payload())
    assert first.success is True

    # Retry with the SAME correlation_id — must be suppressed, no POST.
    calls2: list = []
    client2 = _client_yielding([_FakeResponse({"id": "chk_2", "status": "queued"})], calls2)
    with (
        patch("app.services.payment_adapters.checkeeper.httpx.AsyncClient", client2),
        patch("app.redis.get_redis", AsyncMock(return_value=r)),
    ):
        second = await adapter.create_payment(_payload())

    assert second.success is False
    assert second.failure_reason == "checkeeper_duplicate_suppressed"
    assert calls2 == []  # NO second physical check


@pytest.mark.asyncio
async def test_fresh_correlation_id_is_a_distinct_payment_and_issues():
    """A legitimate re-pay (a new payment attempt) gets a fresh
    correlation_id and its own slot — it is NOT blocked by the prior one."""
    adapter = CheckeeperAdapter({"api_key": "k", "bank_account_id": "bnk"})
    r = _fake_redis()

    with patch("app.redis.get_redis", AsyncMock(return_value=r)):
        calls1: list = []
        c1 = _client_yielding([_FakeResponse({"id": "chk_1", "status": "queued"})], calls1)
        with patch("app.services.payment_adapters.checkeeper.httpx.AsyncClient", c1):
            await adapter.create_payment(_payload(correlation_id="attempt-A"))

        calls2: list = []
        c2 = _client_yielding([_FakeResponse({"id": "chk_2", "status": "queued"})], calls2)
        with patch("app.services.payment_adapters.checkeeper.httpx.AsyncClient", c2):
            second = await adapter.create_payment(_payload(correlation_id="attempt-B"))

    assert second.success is True
    assert len(calls2) == 1  # distinct attempt DID issue


@pytest.mark.asyncio
async def test_redis_outage_fails_closed_without_issuing():
    """No Redis → no idempotency guarantee → refuse to issue (fail closed).
    A physical check must never go out unguarded."""
    adapter = CheckeeperAdapter({"api_key": "k", "bank_account_id": "bnk"})
    calls: list = []
    client = _client_yielding([_FakeResponse({"id": "chk_1"})], calls)

    with (
        patch("app.services.payment_adapters.checkeeper.httpx.AsyncClient", client),
        patch("app.redis.get_redis", AsyncMock(side_effect=RuntimeError("redis down"))),
    ):
        result = await adapter.create_payment(_payload())

    assert result.success is False
    assert result.failure_reason == "checkeeper_idempotency_unavailable"
    assert calls == []  # never POSTed


@pytest.mark.asyncio
async def test_api_rejection_releases_slot_so_a_retry_can_proceed():
    """A clean 4xx from Checkeeper means NO check printed — the slot is
    released so a legitimate retry of the same correlation_id isn't stuck."""
    adapter = CheckeeperAdapter({"api_key": "k", "bank_account_id": "bnk"})
    r = _fake_redis()

    calls: list = []
    client = _client_yielding(
        [_FakeResponse({"code": "invalid_bank_account"}, status_code=422)], calls
    )
    with (
        patch("app.services.payment_adapters.checkeeper.httpx.AsyncClient", client),
        patch("app.redis.get_redis", AsyncMock(return_value=r)),
    ):
        result = await adapter.create_payment(_payload())

    assert result.success is False
    assert result.failure_reason.startswith("checkeeper_api_error")
    # Slot released — a retry can re-claim it.
    assert "checkeeper:payment:cor-idem-1" not in r._store
    r.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_transport_error_keeps_slot_claimed():
    """A transport failure is ambiguous — Checkeeper may have printed before
    the socket died. Keep the slot CLAIMED so an auto-retry can't double-print."""
    adapter = CheckeeperAdapter({"api_key": "k", "bank_account_id": "bnk"})
    r = _fake_redis()

    calls: list = []
    client = _client_yielding([httpx.ConnectError("boom")], calls)
    with (
        patch("app.services.payment_adapters.checkeeper.httpx.AsyncClient", client),
        patch("app.redis.get_redis", AsyncMock(return_value=r)),
    ):
        result = await adapter.create_payment(_payload())

    assert result.success is False
    assert result.failure_reason.startswith("checkeeper_transport_error")
    # Slot retained (fail-closed on ambiguity) — no delete.
    assert "checkeeper:payment:cor-idem-1" in r._store
    r.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_address_never_burns_a_slot():
    """Pre-validation (missing mailing address) returns BEFORE the Redis
    gate — a validation reject must not claim (and thus block) a slot."""
    adapter = CheckeeperAdapter({"api_key": "k", "bank_account_id": "bnk"})
    r = _fake_redis()

    payload = _payload()
    payload.vendor_bank = {}  # no mailing address
    with patch("app.redis.get_redis", AsyncMock(return_value=r)):
        result = await adapter.create_payment(payload)

    assert result.failure_reason == "checkeeper_missing_mailing_address"
    r.set.assert_not_awaited()  # slot never touched
