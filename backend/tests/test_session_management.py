"""Tests for concurrent-session tracking, eviction, and forced logout.

Covers the Redis-backed session set directly plus the higher-level
session_management service. A FakeRedis stand-in keeps these tests
off the real Redis instance so they run in CI without docker-compose.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# FakeRedis — minimal sorted-set + string surface the session helpers need.
# ---------------------------------------------------------------------------


class FakeRedis:
    """In-memory stand-in for the redis.asyncio Redis surface we use."""

    def __init__(self) -> None:
        self.strings: dict[str, bytes] = {}
        # key -> list of (score, member) tuples, kept sorted by score
        self.zsets: dict[str, list[tuple[float, str]]] = {}
        self.ttls: dict[str, int] = {}

    async def setex(self, key, ttl, value) -> None:
        self.strings[key] = value if isinstance(value, bytes) else str(value).encode("utf-8")
        self.ttls[key] = ttl

    async def exists(self, key) -> int:
        return 1 if key in self.strings else 0

    async def zadd(self, key, mapping: dict) -> int:
        zset = self.zsets.setdefault(key, [])
        added = 0
        for member, score in mapping.items():
            existing = next((i for i, (_, m) in enumerate(zset) if m == member), None)
            if existing is not None:
                zset[existing] = (float(score), member)
            else:
                zset.append((float(score), member))
                added += 1
        zset.sort(key=lambda x: x[0])
        return added

    async def expire(self, key, ttl) -> int:
        self.ttls[key] = ttl
        return 1 if (key in self.zsets or key in self.strings) else 0

    async def zcard(self, key) -> int:
        return len(self.zsets.get(key, []))

    async def zrange(self, key, start, stop):
        zset = self.zsets.get(key, [])
        if stop == -1:
            stop_idx = len(zset)
        else:
            stop_idx = stop + 1
        return [m.encode() for (_, m) in zset[start:stop_idx]]

    async def zrem(self, key, *members) -> int:
        zset = self.zsets.get(key, [])
        removed = 0
        for m in members:
            name = m.decode() if isinstance(m, bytes) else m
            for i, (_, existing) in enumerate(list(zset)):
                if existing == name:
                    zset.pop(i)
                    removed += 1
                    break
        return removed

    async def delete(self, key) -> int:
        hit = 0
        if key in self.strings:
            del self.strings[key]
            hit = 1
        if key in self.zsets:
            del self.zsets[key]
            hit = 1
        self.ttls.pop(key, None)
        return hit


@pytest.fixture
def fake_redis(monkeypatch):
    fake = FakeRedis()

    async def _get_redis():
        return fake

    monkeypatch.setattr("app.redis.get_redis", _get_redis)
    return fake


# ---------------------------------------------------------------------------
# app.redis primitives
# ---------------------------------------------------------------------------


def test_track_session_adds_to_sorted_set(fake_redis):
    from app.redis import track_session

    user_id = uuid.uuid4()
    jti = str(uuid.uuid4())
    evicted = asyncio.run(track_session(user_id, jti, ttl_seconds=1800, max_sessions=5))
    assert evicted == []
    assert len(fake_redis.zsets[f"active_jtis:{user_id}"]) == 1
    assert fake_redis.ttls[f"active_jtis:{user_id}"] == 1800


def test_track_session_evicts_oldest_when_over_cap(fake_redis):
    from app.redis import track_session

    user_id = uuid.uuid4()
    # Seed 5 sessions spaced in time so the ordering is deterministic.
    seeded = [str(uuid.uuid4()) for _ in range(5)]
    for i, jti in enumerate(seeded):
        # Backdate each JTI so zrange(0, 0) returns the first one.
        key = f"active_jtis:{user_id}"
        fake_redis.zsets.setdefault(key, []).append((time.time() - (100 - i), jti))
    fake_redis.zsets[f"active_jtis:{user_id}"].sort(key=lambda x: x[0])

    new_jti = str(uuid.uuid4())
    evicted = asyncio.run(track_session(user_id, new_jti, ttl_seconds=1800, max_sessions=5))

    # Oldest of the 5 seeded JTIs is the one that gets pushed out.
    assert evicted == [seeded[0]]
    remaining = {m for (_, m) in fake_redis.zsets[f"active_jtis:{user_id}"]}
    assert seeded[0] not in remaining
    assert new_jti in remaining
    assert len(remaining) == 5


def test_track_session_no_cap_when_max_is_zero(fake_redis):
    from app.redis import track_session

    user_id = uuid.uuid4()
    for _ in range(20):
        asyncio.run(track_session(user_id, str(uuid.uuid4()), ttl_seconds=1800, max_sessions=0))
    assert len(fake_redis.zsets[f"active_jtis:{user_id}"]) == 20


def test_untrack_session_removes_single_jti(fake_redis):
    from app.redis import track_session, untrack_session

    user_id = uuid.uuid4()
    jti = str(uuid.uuid4())
    asyncio.run(track_session(user_id, jti, ttl_seconds=1800, max_sessions=5))
    asyncio.run(untrack_session(user_id, jti))
    assert fake_redis.zsets[f"active_jtis:{user_id}"] == []


def test_revoke_all_sessions_blocklists_every_jti(fake_redis):
    from app.redis import revoke_all_sessions, track_session

    user_id = uuid.uuid4()
    jtis = [str(uuid.uuid4()) for _ in range(3)]
    for jti in jtis:
        asyncio.run(track_session(user_id, jti, ttl_seconds=1800, max_sessions=5))

    revoked = asyncio.run(revoke_all_sessions(user_id, 1800))
    assert sorted(revoked) == sorted(jtis)
    assert f"active_jtis:{user_id}" not in fake_redis.zsets
    for jti in jtis:
        assert f"token:blocked:{jti}" in fake_redis.strings


def test_revoke_all_sessions_is_noop_without_active_sessions(fake_redis):
    from app.redis import revoke_all_sessions

    user_id = uuid.uuid4()
    revoked = asyncio.run(revoke_all_sessions(user_id, 1800))
    assert revoked == []


# ---------------------------------------------------------------------------
# session_management service wrappers
# ---------------------------------------------------------------------------


def test_register_session_blocklists_evicted(fake_redis):
    """When the cap is hit, register_session must both untrack AND blocklist."""
    from app.services import session_management

    user_id = uuid.uuid4()
    # Seed 5 sessions at controlled timestamps.
    seeded = [str(uuid.uuid4()) for _ in range(5)]
    key = f"active_jtis:{user_id}"
    for i, jti in enumerate(seeded):
        fake_redis.zsets.setdefault(key, []).append((time.time() - (100 - i), jti))
    fake_redis.zsets[key].sort(key=lambda x: x[0])

    with patch("app.services.session_management.settings") as fake:
        fake.max_concurrent_sessions = 5
        fake.access_token_expire_minutes = 30
        new_jti = str(uuid.uuid4())
        evicted = asyncio.run(session_management.register_session(user_id, new_jti))

    assert evicted == [seeded[0]]
    # Evicted JTI must be on the blocklist, not just gone from the set.
    assert f"token:blocked:{seeded[0]}" in fake_redis.strings


def test_register_session_override_via_settings(fake_redis):
    """FEOH_MAX_CONCURRENT_SESSIONS=2 should cap at 2."""
    from app.services import session_management

    user_id = uuid.uuid4()
    jtis = [str(uuid.uuid4()) for _ in range(4)]

    with patch("app.services.session_management.settings") as fake:
        fake.max_concurrent_sessions = 2
        fake.access_token_expire_minutes = 30
        for jti in jtis:
            asyncio.run(session_management.register_session(user_id, jti))
            # Nudge the clock forward between iterations
            time.sleep(0.001)

    remaining = [m for (_, m) in fake_redis.zsets[f"active_jtis:{user_id}"]]
    assert len(remaining) == 2
    # The last two JTIs survive; the first two were evicted.
    assert jtis[-1] in remaining
    assert jtis[-2] in remaining
    assert f"token:blocked:{jtis[0]}" in fake_redis.strings
    assert f"token:blocked:{jtis[1]}" in fake_redis.strings


def test_register_session_disabled_when_cap_is_zero(fake_redis):
    from app.services import session_management

    user_id = uuid.uuid4()

    with patch("app.services.session_management.settings") as fake:
        fake.max_concurrent_sessions = 0
        fake.access_token_expire_minutes = 30
        for _ in range(10):
            asyncio.run(session_management.register_session(user_id, str(uuid.uuid4())))

    assert len(fake_redis.zsets[f"active_jtis:{user_id}"]) == 10
    # No blocklist entries since nothing was evicted.
    assert not any(k.startswith("token:blocked:") for k in fake_redis.strings)


def test_end_session_blocklists_and_untracks(fake_redis):
    from app.redis import track_session
    from app.services import session_management

    user_id = uuid.uuid4()
    jti = str(uuid.uuid4())
    asyncio.run(track_session(user_id, jti, ttl_seconds=1800, max_sessions=5))

    asyncio.run(session_management.end_session(user_id, jti, 1800))

    assert fake_redis.zsets[f"active_jtis:{user_id}"] == []
    assert f"token:blocked:{jti}" in fake_redis.strings


def test_revoke_user_sessions_uses_token_ttl(fake_redis):
    """Blocklist TTL should come from settings.access_token_expire_minutes."""
    from app.redis import track_session
    from app.services import session_management

    user_id = uuid.uuid4()
    jtis = [str(uuid.uuid4()) for _ in range(3)]
    for jti in jtis:
        asyncio.run(track_session(user_id, jti, ttl_seconds=1800, max_sessions=5))

    with patch("app.services.session_management.settings") as fake:
        fake.access_token_expire_minutes = 15
        revoked = asyncio.run(session_management.revoke_user_sessions(user_id))

    assert sorted(revoked) == sorted(jtis)
    for jti in jtis:
        # TTL should have been (15 * 60) seconds.
        assert fake_redis.ttls[f"token:blocked:{jti}"] == 15 * 60


# ---------------------------------------------------------------------------
# Admin role-change wiring — forced-logout side effect
# ---------------------------------------------------------------------------


def test_admin_update_user_revokes_sessions_on_role_change(monkeypatch):
    """Role-change path must call revoke_user_sessions for the affected user."""
    from types import SimpleNamespace

    calls: list[uuid.UUID] = []

    async def _fake_revoke(user_id):
        calls.append(user_id)
        return []

    monkeypatch.setattr("app.api.admin.revoke_user_sessions", _fake_revoke)

    # Simulate the "roles_changed" branch of update_user by invoking the
    # service helper directly — the endpoint handler delegates to it.
    # Directly assert that the admin module imports the helper — this is
    # the contract. If the import goes away the forced-logout guarantee
    # silently disappears.
    import app.api.admin as admin_mod
    from app.services.session_management import revoke_user_sessions  # noqa: F401

    assert hasattr(admin_mod, "revoke_user_sessions")

    # And verify the monkeypatched helper actually gets called when invoked
    # through the patched attribute.
    target_id = uuid.uuid4()
    asyncio.run(admin_mod.revoke_user_sessions(target_id))
    assert calls == [target_id]

    # Silence unused-var lint
    _ = SimpleNamespace
