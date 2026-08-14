"""Session management — the concurrent-session limit, forced logout on role
change, and the user-facing "where you're signed in" list.

Separates the "how" (Redis bookkeeping) from the "when" (login / logout /
admin mutations). Callers stay focused on their primary job and the SOC 2
requirements live in one place.

The descriptive layer (`SessionInfo`, `describe_user_agent`) exists because a
bare JTI is not something a human can act on: to answer "is that other session
mine?" a user needs the network it signed in from, the kind of device, and when.
The metadata is written once at sign-in and lives only as long as the session
does — see `app/redis.py` § SESSION_META_PREFIX.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from app.config import settings
from app.redis import (
    block_token,
    drop_sessions,
    get_active_sessions_with_scores,
    get_session_meta,
    revoke_all_sessions,
    revoke_session,
    track_session,
    untrack_session,
)

logger = logging.getLogger(__name__)

# Upper bound on anything we echo back from a request into Redis. The IP comes
# from a trusted proxy header on deployed stacks, but "trusted" is not the same
# as "bounded" — a malformed forwarded-for must not be able to grow a session
# record without limit.
_MAX_STORED_LEN = 64


def _access_token_ttl_seconds() -> int:
    return max(int(settings.access_token_expire_minutes * 60), 1)


@dataclass(frozen=True)
class SessionInfo:
    """One tracked sign-in, as shown back to the account holder."""

    jti: str
    created_at: datetime
    expires_at: datetime
    ip: str | None
    device: str | None
    method: str | None


# ---------------------------------------------------------------------------
# User-agent → short device label (pure)
# ---------------------------------------------------------------------------

# Ordered browser probes. `Edg` and `OPR` must be tested before `Chrome`, and
# `Chrome` before `Safari`, because Chromium browsers carry every earlier
# token in their UA string for historical compatibility.
_BROWSERS: tuple[tuple[str, str], ...] = (
    ("Edg", "Edge"),
    ("OPR", "Opera"),
    ("Chrome", "Chrome"),
    ("Firefox", "Firefox"),
    ("Safari", "Safari"),
)

# Ordered OS probes. Mobile tokens come first: an Android UA also contains
# "Linux", and iPadOS reports "Mac OS X" alongside "iPad".
_PLATFORMS: tuple[tuple[str, str], ...] = (
    ("iPhone", "iPhone"),
    ("iPad", "iPad"),
    ("Android", "Android"),
    ("CrOS", "ChromeOS"),
    ("Windows", "Windows"),
    ("Mac OS X", "macOS"),
    ("Macintosh", "macOS"),
    ("Linux", "Linux"),
)


def describe_user_agent(user_agent: str | None) -> str | None:
    """Reduce a User-Agent string to a short "Chrome on macOS" style label.

    Deliberately coarse. The label exists so a user can recognise their own
    devices in the session list, not to fingerprint them — so the raw UA (a
    high-entropy tracking vector) is never stored, only this derived summary.
    Returns ``None`` when nothing recognisable is present, which the API then
    renders as an unknown device rather than inventing a label.
    """
    if not user_agent:
        return None
    ua = user_agent[:400]
    browser = next((label for token, label in _BROWSERS if token in ua), None)
    platform = next((label for token, label in _PLATFORMS if token in ua), None)
    if browser and platform:
        return f"{browser} on {platform}"
    return browser or platform


def _clip(value: str | None) -> str | None:
    if not value:
        return None
    return value[:_MAX_STORED_LEN]


def _encode_meta(
    *, ip: str | None, device: str | None, method: str | None, ttl_seconds: int
) -> str:
    payload = {k: v for k, v in (("ip", ip), ("device", device), ("method", method)) if v}
    # The token lifetime IN FORCE AT SIGN-IN, recorded per session. Deriving a
    # session's expiry from the *current* setting instead would mean that
    # shortening `FEOH_ACCESS_TOKEN_EXPIRE_MINUTES` makes every session minted
    # under the old value look already-expired — so `list_sessions` would prune
    # them while their tokens keep authenticating, leaving a live session the
    # user can neither see nor revoke. That is precisely the failure this
    # feature exists to prevent.
    payload["ttl"] = str(ttl_seconds)
    return json.dumps(payload, separators=(",", ":"))


def _session_ttl(detail: dict[str, str], fallback: int) -> int:
    """The lifetime recorded for this session, or the current default.

    The fallback covers a session tracked before the `ttl` field existed, and
    any value that didn't survive the round trip intact.
    """
    raw = detail.get("ttl")
    if raw is None:
        return fallback
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return fallback
    return value if value > 0 else fallback


def _decode_meta(raw: str | None) -> dict[str, str]:
    """Parse a stored metadata blob, tolerating anything unexpected.

    A malformed blob must degrade to "no detail known" — never break the
    session list, which is the very screen a user reaches for when they suspect
    something is wrong with their account.
    """
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {k: v for k, v in parsed.items() if isinstance(k, str) and isinstance(v, str)}


async def register_session(
    user_id: uuid.UUID,
    jti: str,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
    method: str | None = None,
) -> list[str]:
    """Track a newly-minted JTI for the user and enforce the concurrent cap.

    Evicted JTIs are added to the blocklist so the evicted sessions stop
    authenticating on the next request. Returns the evicted JTIs (empty
    list when under the cap or when the cap is disabled).

    `ip` / `user_agent` / `method` are recorded alongside so the account holder
    can later tell their own sessions apart. All three are optional — a caller
    that has no request context simply records a bare session.
    """
    ttl = _access_token_ttl_seconds()
    evicted = await track_session(
        user_id=user_id,
        jti=jti,
        ttl_seconds=ttl,
        max_sessions=settings.max_concurrent_sessions,
        meta=_encode_meta(
            ip=_clip(ip),
            device=_clip(describe_user_agent(user_agent)),
            method=_clip(method),
            ttl_seconds=ttl,
        ),
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


async def list_sessions(user_id: uuid.UUID) -> list[SessionInfo]:
    """Return the user's live sessions, newest first.

    Entries whose access token has already expired on its own are pruned in
    passing rather than listed. They accumulate because the tracking set's TTL
    is refreshed on *every* sign-in, so a set kept alive by recent logins can
    outlive the individual tokens inside it — listing those would show phantom
    sessions the user cannot meaningfully revoke.

    Expiry is judged against the lifetime recorded FOR THAT SESSION, not the
    current setting, so changing `FEOH_ACCESS_TOKEN_EXPIRE_MINUTES` can't prune
    a session whose token is still authenticating (see `_encode_meta`).
    """
    default_ttl = _access_token_ttl_seconds()
    now = datetime.now(UTC)
    tracked = await get_active_sessions_with_scores(user_id)
    if not tracked:
        return []
    meta = await get_session_meta(user_id)

    live: list[SessionInfo] = []
    stale: list[str] = []
    for jti, issued_at in tracked:
        detail = _decode_meta(meta.get(jti))
        created_at = datetime.fromtimestamp(issued_at, tz=UTC)
        expires_at = datetime.fromtimestamp(issued_at + _session_ttl(detail, default_ttl), tz=UTC)
        if expires_at <= now:
            stale.append(jti)
            continue
        live.append(
            SessionInfo(
                jti=jti,
                created_at=created_at,
                expires_at=expires_at,
                ip=detail.get("ip"),
                device=detail.get("device"),
                method=detail.get("method"),
            )
        )
    await drop_sessions(user_id, stale)
    live.sort(key=lambda s: s.created_at, reverse=True)
    return live


async def revoke_one_session(user_id: uuid.UUID, jti: str) -> bool:
    """Blocklist a single session of this user. False when it isn't tracked."""
    revoked = await revoke_session(user_id, jti, _access_token_ttl_seconds())
    if revoked:
        logger.info("Revoked 1 session for user %s", user_id)
    return revoked


async def revoke_other_sessions(user_id: uuid.UUID, keep_jti: str | None) -> list[str]:
    """Blocklist every session for the user EXCEPT `keep_jti`.

    The "sign out everywhere else" action. `keep_jti` is the caller's own
    session; passing ``None`` degrades to signing out everywhere including the
    caller, which is the safe direction if the current JTI can't be resolved.
    """
    ttl = _access_token_ttl_seconds()
    revoked = await revoke_all_sessions(user_id, ttl, except_jti=keep_jti)
    if revoked:
        logger.info("Revoked %d other session(s) for user %s", len(revoked), user_id)
    return revoked


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
