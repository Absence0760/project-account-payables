"""Redis-backed sliding-window rate limiter.

Used to cap abuse vectors on unauthenticated endpoints (signup, captcha).
Each bucket is a Redis key holding a sorted-set of timestamps; we trim
entries older than the window and count what remains. ZADD/ZREMRANGEBYSCORE/
ZCARD are all O(log n) and run in a single MULTI.

Scope: one counter per (endpoint, client IP). For shared-IP networks this
is coarse but acceptable for the low-volume abuse surface of tenant signup.
"""

from __future__ import annotations

import time
import uuid

from fastapi import HTTPException, Request, status

from app.redis import get_redis

KEY_PREFIX = "ratelimit:"


class RateLimitExceeded(HTTPException):
    def __init__(self, retry_after_seconds: int):
        super().__init__(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Too many requests. Please wait {retry_after_seconds} seconds before trying again."
            ),
            headers={"Retry-After": str(retry_after_seconds)},
        )


def _client_ip(request: Request) -> str:
    # Prefer the first forwarded-for hop if an ALB/CF is in front.
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


async def check_rate_limit(
    endpoint: str,
    request: Request,
    *,
    limit: int,
    window_seconds: int,
) -> None:
    """Record this request and raise 429 if the caller is over the limit."""
    client = _client_ip(request)
    key = f"{KEY_PREFIX}{endpoint}:{client}"
    now = time.time()
    cutoff = now - window_seconds

    r = await get_redis()
    async with r.pipeline(transaction=True) as pipe:
        pipe.zremrangebyscore(key, 0, cutoff)
        pipe.zadd(key, {str(uuid.uuid4()): now})
        pipe.zcard(key)
        pipe.expire(key, window_seconds)
        _, _, count, _ = await pipe.execute()

    if count > limit:
        # Retry-After: time until oldest entry in the window ages out.
        oldest = await r.zrange(key, 0, 0, withscores=True)
        if oldest:
            wait = int(oldest[0][1] + window_seconds - now)
            raise RateLimitExceeded(max(wait, 1))
        raise RateLimitExceeded(window_seconds)
