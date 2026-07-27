"""Redis-backed sliding-window rate limiter.

Used to cap abuse vectors on unauthenticated endpoints (signup, captcha).
Each bucket is a Redis key holding a sorted-set of timestamps; we trim
entries older than the window and count what remains. ZADD/ZREMRANGEBYSCORE/
ZCARD are all O(log n) and run in a single MULTI.

Scope: one counter per (endpoint, client IP). For shared-IP networks this
is coarse but acceptable for the low-volume abuse surface of tenant signup.
"""

from __future__ import annotations

import ipaddress
import time
import uuid

from fastapi import HTTPException, Request, status

from app.config import settings
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


def _trusted_proxy_networks() -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    raw = (settings.trusted_proxy_cidrs or "").strip()
    if not raw:
        return ()
    nets: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            nets.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            # Skip junk silently — a typo in config shouldn't crash the
            # rate limiter, and any unknown CIDR means "don't trust XFF
            # from anything", which is the safe default anyway.
            continue
    return tuple(nets)


def _ip_in_trusted_proxy(ip: str) -> bool:
    nets = _trusted_proxy_networks()
    if not nets:
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in n for n in nets)


def resolve_client_ip(request: Request | None) -> str | None:
    """Resolve the originating client IP.

    Only honours ``X-Forwarded-For`` when the connecting peer is in the
    configured ``trusted_proxy_cidrs`` allowlist — otherwise a direct
    attacker could rotate through arbitrary IPs by spoofing the header
    and dodge per-IP rate limits.

    This is the single shared resolver: every request-path caller that
    records or keys on a client IP (limiter buckets, login/signup/SSO
    audit rows, captcha verification) goes through here, so they all
    agree on who the client is when the app sits behind a trusted proxy
    (Caddy on the single-VM stack, the ALB on ECS).
    """
    if request is None or request.client is None:
        return None
    peer = request.client.host
    if _ip_in_trusted_proxy(peer):
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return peer


def _client_ip(request: Request) -> str:
    return resolve_client_ip(request) or "unknown"


async def check_rate_limit(
    endpoint: str,
    request: Request | None = None,
    *,
    limit: int,
    window_seconds: int,
    subject: str | None = None,
) -> None:
    """Record this request and raise 429 if the caller is over the limit.

    The bucket is keyed on ``(endpoint, subject)``. ``subject`` defaults to the
    resolved client IP, but callers can pass an explicit value — e.g. the
    target email address, or an authenticated API key id — to cap abuse that a
    per-IP limit can't (an attacker rotating IPs to email-bomb one victim
    address, or a single API key flooding the public API). When ``subject`` is
    given the ``request`` is unused, so callers keying on an explicit subject
    may omit it; only the IP-fallback path needs the request.

    No-ops when ``settings.rate_limit_enabled`` is False. The switch
    exists so CI's e2e job (where every shard's 4 workers hit
    ``/api/auth/login`` from the same loopback IP) doesn't saturate
    the 10/min cap before the storage-state preload finishes.
    Deployed envs keep the limiter on by default.
    """
    if not settings.rate_limit_enabled:
        return

    if subject is not None:
        client = subject
    elif request is not None:
        client = _client_ip(request)
    else:
        raise ValueError("check_rate_limit requires either a request or an explicit subject")
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
