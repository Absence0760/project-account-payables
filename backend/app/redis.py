"""Redis client and token blocklist."""

import redis.asyncio as redis

from app.config import settings

pool = redis.ConnectionPool.from_url(settings.redis_url)

BLOCKLIST_PREFIX = "token:blocked:"


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
