"""Shared pytest fixtures.

The auth + portal-auth handlers now call ``check_rate_limit`` at their
entry points, which in turn opens a Redis connection. The vast majority
of the suite is DB-free / Redis-free, so we install an in-memory
sorted-set fake under ``app.services.rate_limit.get_redis`` for every
test by default. Tests that *do* want to exercise the real limiter
(only ``test_rate_limit_security.py`` today) install their own fake via
the local ``fake_redis`` fixture and override this one.
"""

from __future__ import annotations

import pytest


class _FakeSortedSet:
    def __init__(self) -> None:
        self.store: dict[str, list[tuple[str, float]]] = {}

    def zadd(self, key, mapping) -> None:
        self.store.setdefault(key, [])
        for member, score in mapping.items():
            self.store[key].append((member, score))

    def zremrangebyscore(self, key, low, high) -> None:
        if key in self.store:
            self.store[key] = [(m, s) for m, s in self.store[key] if not (low <= s <= high)]

    def zcard(self, key) -> int:
        return len(self.store.get(key, []))

    def zrange(self, key, start, stop, withscores=False):
        items = sorted(self.store.get(key, []), key=lambda t: t[1])
        slice_ = items[start : stop + 1 if stop >= 0 else None]
        if withscores:
            return list(slice_)
        return [m for m, _ in slice_]


class _FakePipeline:
    def __init__(self, sset: _FakeSortedSet) -> None:
        self._sset = sset
        self._calls: list = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def zremrangebyscore(self, key, low, high) -> None:
        self._calls.append(("zremrangebyscore", key, low, high))

    def zadd(self, key, mapping) -> None:
        self._calls.append(("zadd", key, mapping))

    def zcard(self, key) -> None:
        self._calls.append(("zcard", key))

    def expire(self, key, ttl) -> None:
        self._calls.append(("expire", key, ttl))

    async def execute(self):
        results = []
        for call in self._calls:
            op = call[0]
            if op == "zremrangebyscore":
                self._sset.zremrangebyscore(call[1], call[2], call[3])
                results.append(None)
            elif op == "zadd":
                self._sset.zadd(call[1], call[2])
                results.append(None)
            elif op == "zcard":
                results.append(self._sset.zcard(call[1]))
            elif op == "expire":
                results.append(True)
        return results


class _FakeRedis:
    def __init__(self) -> None:
        self.sset = _FakeSortedSet()
        # `is_event_already_processed` uses `SET k v NX EX <ttl>` to dedupe
        # webhook events. The in-memory dict here is enough to make the
        # first delivery win and subsequent retries short-circuit.
        self._kv: dict[str, str] = {}

    def pipeline(self, transaction: bool = True):  # noqa: ARG002
        return _FakePipeline(self.sset)

    async def zrange(self, key, start, stop, withscores=False):
        return self.sset.zrange(key, start, stop, withscores=withscores)

    async def set(
        self,
        key: str,
        value: str,
        nx: bool = False,
        ex: int | None = None,
        **_kwargs,
    ):
        if nx and key in self._kv:
            return None
        self._kv[key] = value
        return True

    async def get(self, key: str):
        return self._kv.get(key)

    async def delete(self, *keys: str) -> int:
        count = 0
        for k in keys:
            if k in self._kv:
                del self._kv[k]
                count += 1
        return count


@pytest.fixture(autouse=True)
def _autouse_fake_redis(monkeypatch):
    """Stub Redis out of the rate limiter + webhook event dedup ledger
    for every test by default.

    A test that wants to exercise the real limit (mostly the dedicated
    rate-limit security tests) can ignore this fixture or override the
    target ``get_redis`` itself. Keeping the stub here means new
    rate-limited endpoints / event-deduped webhook handlers don't drag
    a Redis dependency into every otherwise-pure test file.
    """
    fake = _FakeRedis()

    async def _get_redis():
        return fake

    monkeypatch.setattr("app.services.rate_limit.get_redis", _get_redis)
    monkeypatch.setattr("app.services.webhook_security.get_redis", _get_redis)
    return fake
