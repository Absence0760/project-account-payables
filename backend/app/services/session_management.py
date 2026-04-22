"""Session management — concurrent-session limit + forced logout on role change.

Separates the "how" (Redis bookkeeping) from the "when" (login / logout /
admin mutations). Callers stay focused on their primary job and the SOC 2
requirements live in one place.
"""

from __future__ import annotations

import logging
import uuid

from app.config import settings
from app.redis import (
    block_token,
    revoke_all_sessions,
    track_session,
    untrack_session,
)

logger = logging.getLogger(__name__)


def _access_token_ttl_seconds() -> int:
    return max(int(settings.access_token_expire_minutes * 60), 1)


async def register_session(user_id: uuid.UUID, jti: str) -> list[str]:
    """Track a newly-minted JTI for the user and enforce the concurrent cap.

    Evicted JTIs are added to the blocklist so the evicted sessions stop
    authenticating on the next request. Returns the evicted JTIs (empty
    list when under the cap or when the cap is disabled).
    """
    ttl = _access_token_ttl_seconds()
    evicted = await track_session(
        user_id=user_id,
        jti=jti,
        ttl_seconds=ttl,
        max_sessions=settings.max_concurrent_sessions,
    )
    for evicted_jti in evicted:
        await block_token(evicted_jti, ttl)
    if evicted:
        logger.info(
            "Evicted %d oldest session(s) for user %s due to concurrent-session cap",
            len(evicted),
            user_id,
        )
    return evicted


async def end_session(user_id: uuid.UUID, jti: str, expires_in: int) -> None:
    """Drop a single JTI from the user's active-session set AND add it to
    the blocklist. Used by the logout endpoint.
    """
    await block_token(jti, expires_in)
    await untrack_session(user_id, jti)


async def revoke_user_sessions(user_id: uuid.UUID) -> list[str]:
    """Blocklist + forget every active JTI for the user.

    Called by admin role change and deactivation — the existing sessions
    must no longer carry the old permissions (SOC 2 CC6.1 / CC6.2).
    Returns the JTIs that were revoked.
    """
    ttl = _access_token_ttl_seconds()
    revoked = await revoke_all_sessions(user_id, ttl)
    if revoked:
        logger.info("Revoked %d active session(s) for user %s", len(revoked), user_id)
    return revoked
