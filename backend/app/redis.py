"""Redis client, token blocklist, and per-user active-session tracking."""

import time

import redis.asyncio as redis

from app.config import settings

pool = redis.ConnectionPool.from_url(settings.redis_url)

BLOCKLIST_PREFIX = "token:blocked:"
SESSION_SET_PREFIX = "active_jtis:"


async def get_redis() -> redis.Redis:
    return redis.Redis(connection_pool=pool)


async def block_token(jti: str, expires_in: int) -> None:
    """Add a token ID to the blocklist. Expires automatically with the token."""
    r = await get_redis()
    await r.setex(f"{BLOCKLIST_PREFIX}{jti}", expires_in, "1")


async def is_token_blocked(jti: str) -> bool:
    """Check if a token has been blocklisted (logged out)."""
    r = await get_redis()
    return await r.exists(f"{BLOCKLIST_PREFIX}{jti}") > 0


# ---------------------------------------------------------------------------
# Per-user active-session tracking (concurrent session limit + forced logout)
#
# Each login adds the newly-minted JTI to a Redis sorted set keyed by user,
# scored by issue time (epoch seconds). This lets us:
#   - Enforce a concurrent-session cap: evict the lowest-scored (oldest) JTI
#     when the set exceeds the configured max.
#   - Revoke every session for a user on admin role change / deactivation.
# The set itself carries a TTL equal to the access-token lifetime so stale
# JTIs age out naturally if a user stops logging in.
# ---------------------------------------------------------------------------


def _session_key(user_id) -> str:
    return f"{SESSION_SET_PREFIX}{user_id}"


async def track_session(user_id, jti: str, ttl_seconds: int, max_sessions: int) -> list[str]:
    """Record a new session JTI for a user and enforce the concurrent-session cap.

    Returns the list of JTIs that were evicted (oldest-first) because the
    user was over the cap. Callers should add these to the blocklist so the
    evicted tokens stop authenticating immediately.

    If `max_sessions` is 0 or negative, no cap is enforced.
    """
    r = await get_redis()
    key = _session_key(user_id)
    now = time.time()

    # ZADD — score = issue time so ZRANGE returns oldest-first.
    await r.zadd(key, {jti: now})
    # Refresh TTL on the set so it doesn't outlive the token lifetime by much.
    # Set is refreshed on every login, which is what we want — inactive users
    # have their set evicted by Redis automatically.
    await r.expire(key, ttl_seconds)

    evicted: list[str] = []
    if max_sessions and max_sessions > 0:
        count = await r.zcard(key)
        if count > max_sessions:
            # Oldest first (lowest score). Pop (count - max_sessions) entries.
            overflow = count - max_sessions
            members = await r.zrange(key, 0, overflow - 1)
            for m in members:
                jti_str = m.decode() if isinstance(m, bytes) else m
                evicted.append(jti_str)
            if members:
                await r.zrem(key, *members)
    return evicted


async def untrack_session(user_id, jti: str) -> None:
    """Remove a single JTI from the user's active set (called on logout)."""
    r = await get_redis()
    await r.zrem(_session_key(user_id), jti)


async def get_active_sessions(user_id) -> list[str]:
    """Return the JTIs currently tracked as active for the user."""
    r = await get_redis()
    members = await r.zrange(_session_key(user_id), 0, -1)
    return [m.decode() if isinstance(m, bytes) else m for m in members]


async def revoke_all_sessions(user_id, ttl_seconds: int) -> list[str]:
    """Blocklist every active JTI for the user and clear the tracking set.

    Used on admin role change and account deactivation — SOC 2 expects an
    actor's active sessions to drop the moment their permissions change.
    Returns the list of JTIs that were revoked.
    """
    r = await get_redis()
    key = _session_key(user_id)
    members = await r.zrange(key, 0, -1)
    jtis = [m.decode() if isinstance(m, bytes) else m for m in members]
    for jti in jtis:
        await r.setex(f"{BLOCKLIST_PREFIX}{jti}", ttl_seconds, "1")
    if jtis:
        await r.delete(key)
    return jtis
