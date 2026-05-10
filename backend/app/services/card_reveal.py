"""Single-use card-reveal tokens for vendor email links.

When we issue a virtual card to a vendor, we email them a one-time link
of the form `https://<slug>.app.com/portal/cards/{token}`. The portal
swaps the token for the card detail (PAN, CVV, expiry) once. After
that, the token is dead.

The plaintext token lives only in the email; we persist a sha256 hash
so a database leak doesn't expose live PANs.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.virtual_card import CardRevealToken, VirtualCard

logger = logging.getLogger(__name__)

# Long enough to push attackers towards brute force as the only option,
# short enough to fit on a phone screen line. 32 bytes = 43 URL-safe chars.
TOKEN_BYTES = 32
DEFAULT_EXPIRY_DAYS = 7


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def mint_reveal_token(
    db: AsyncSession,
    card: VirtualCard,
    *,
    expires_in_days: int = DEFAULT_EXPIRY_DAYS,
) -> str:
    """Persist a fresh CardRevealToken row for `card` and return the
    plaintext token (never persisted; goes straight into the email).

    Caller is responsible for `db.flush` / `db.commit`."""
    token = secrets.token_urlsafe(TOKEN_BYTES)
    row = CardRevealToken(
        token_hash=_hash(token),
        card_id=card.id,
        organization_id=card.organization_id,
        expires_at=datetime.now(UTC) + timedelta(days=expires_in_days),
    )
    db.add(row)
    return token


async def consume_reveal_token(
    db: AsyncSession, token: str
) -> tuple[VirtualCard | None, str | None]:
    """Look up the token; return (card, error_reason).

    Marks the row as used on first successful reveal. Subsequent calls
    with the same plaintext token return (None, "used"). Expired tokens
    return (None, "expired"); unknown tokens return (None, "invalid").

    Caller commits.
    """
    if not token:
        return None, "invalid"

    result = await db.execute(
        select(CardRevealToken).where(CardRevealToken.token_hash == _hash(token))
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None, "invalid"

    now = datetime.now(UTC)
    if row.expires_at < now:
        return None, "expired"
    if row.used_at is not None:
        return None, "used"

    card_result = await db.execute(select(VirtualCard).where(VirtualCard.id == row.card_id))
    card = card_result.scalar_one_or_none()
    if card is None:
        return None, "invalid"

    row.used_at = now
    return card, None
