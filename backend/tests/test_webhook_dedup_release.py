"""Dedup-claim durability primitives (`webhook_security`).

Two bugs these close:

1. The Redis `SET NX` dedup claim is durable the instant it's written, but the
   side effect it guards isn't durable until the DB commits. A commit that rolls
   back AFTER the claim would strand the event id as "processed", so the
   provider's retry is deduped away and the one-time effect (rebate mint, charge
   transition) is lost. `release_event_claim` frees the claim so the retry can
   reprocess.

2. The dedup TTL must cover the LONGEST provider retry window (Lithic ~72h) —
   a shorter TTL lets a late redelivery slip past the dedup and replay.
"""

from __future__ import annotations

import time

import pytest


class _FakeRedis:
    def __init__(self):
        self.store: dict[str, tuple[float | None, str]] = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        expiry = time.time() + ex if ex else None
        self.store[key] = (expiry, value)
        return True

    async def delete(self, key):
        self.store.pop(key, None)
        return 1


@pytest.fixture
def fake_redis(monkeypatch):
    fake = _FakeRedis()

    async def _get_redis():
        return fake

    monkeypatch.setattr("app.services.webhook_security.get_redis", _get_redis)
    return fake


def test_dedup_ttl_covers_the_longest_provider_retry_window():
    from app.services.webhook_security import DEFAULT_DEDUP_TTL_SECONDS

    # Lithic retries a failed delivery over ~3 days. A shorter TTL would let a
    # redelivery after expiry replay the one-time effect.
    assert DEFAULT_DEDUP_TTL_SECONDS >= 72 * 60 * 60


@pytest.mark.asyncio
async def test_release_makes_a_claimed_event_processable_again(fake_redis):
    from app.services.webhook_security import (
        is_event_already_processed,
        release_event_claim,
    )

    # First delivery claims the event.
    assert await is_event_already_processed("lithic", "evt_rollback") is False
    # A retry before release is (correctly) deduped.
    assert await is_event_already_processed("lithic", "evt_rollback") is True

    # The guarded commit failed — release the claim.
    await release_event_claim("lithic", "evt_rollback")

    # The provider's retry can now reprocess (not silently deduped away).
    assert await is_event_already_processed("lithic", "evt_rollback") is False


@pytest.mark.asyncio
async def test_release_of_empty_event_id_is_a_noop(fake_redis):
    from app.services.webhook_security import release_event_claim

    # Nothing was ever claimed for an empty event id — must not raise.
    await release_event_claim("lithic", "")


@pytest.mark.asyncio
async def test_release_never_raises_on_redis_failure(monkeypatch):
    from app.services.webhook_security import release_event_claim

    class _BoomRedis:
        async def delete(self, key):
            raise RuntimeError("redis down")

    async def _get_redis():
        return _BoomRedis()

    monkeypatch.setattr("app.services.webhook_security.get_redis", _get_redis)
    # Best-effort: a Redis hiccup falls back to TTL expiry, never propagates.
    await release_event_claim("lithic", "evt_x")
