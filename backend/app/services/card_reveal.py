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
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, exists, select, update
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


async def _classify_claim_failure(
    db: AsyncSession,
    token_hash: str,
    organization_id: uuid.UUID | None,
    now: datetime,
) -> str:
    """Why did the atomic claim match no row? Read-only; never mutates.

    Runs only on the losing path, so the cost is paid by failures. The
    ordering (expired before used) matches the reason the reveal page shows.
    A live-but-unclaimable token means the card cross-check failed — reported
    as the same opaque ``invalid`` an unknown token gets, so nothing enumerates.
    """
    stmt = select(CardRevealToken.expires_at, CardRevealToken.used_at).where(
        CardRevealToken.token_hash == token_hash
    )
    if organization_id is not None:
        stmt = stmt.where(CardRevealToken.organization_id == organization_id)
    row = (await db.execute(stmt)).one_or_none()
    if row is None:
        return "invalid"
    expires_at, used_at = row
    if expires_at is not None and expires_at < now:
        return "expired"
    if used_at is not None:
        return "used"
    return "invalid"


async def consume_reveal_token(
    db: AsyncSession,
    token: str,
    *,
    organization_id: uuid.UUID | None = None,
) -> tuple[VirtualCard | None, str | None]:
    """Atomically claim the token; return (card, error_reason).

    The claim is a **single** ``UPDATE card_reveal_tokens SET used_at = now()
    WHERE token_hash = … AND used_at IS NULL AND expires_at > now() …
    RETURNING card_id``. Postgres evaluates the predicate under the row lock,
    so of N concurrent requests carrying the same plaintext token EXACTLY ONE
    updates a row and gets the card; every other one matches zero rows and is
    told why by :func:`_classify_claim_failure` (``used`` / ``expired`` /
    ``invalid``). A read-then-write pair could not do this — both readers would
    observe ``used_at IS NULL`` and both would reveal the live PAN/CVV.

    A loser blocks only for as long as the winner's transaction holds the row
    lock, which is why the caller must COMMIT the claim before making any
    outbound provider call (see ``api/portal.py::reveal_card``) — never hold
    this lock across network I/O.

    Rolling the caller's transaction back releases the claim (the token is
    consumable again), which is the correct semantics for "we decided not to
    reveal anything after all" — but it is NOT a safe way to recover from a
    reveal that may already have leaked the PAN. See the handler for the
    fail-closed rule.

    Defense-in-depth tenant binding: when ``organization_id`` is supplied
    (the resolved tenant's ``Organization.id``), BOTH the token row and the
    card it points at must carry that org id, otherwise the reveal is
    refused as ``invalid`` — a mismatch (the same opaque "invalid" the
    unknown-token path returns, so it never enumerates). The card check is an
    ``EXISTS`` inside the claim's own ``WHERE``, so a mismatched card means the
    ``UPDATE`` matches nothing and ``used_at`` is never stamped: nothing is
    burned on a rejected reveal. This is belt-and-suspenders on top of
    ``get_tenant_db`` already landing us in the right tenant DB: the token's own
    recorded ``organization_id`` is verified, not merely assumed from which DB
    the query ran against.

    Caller commits.
    """
    if not token:
        return None, "invalid"

    token_hash = _hash(token)
    now = datetime.now(UTC)

    card_match = VirtualCard.id == CardRevealToken.card_id
    if organization_id is not None:
        card_match = and_(card_match, VirtualCard.organization_id == organization_id)

    claim = (
        update(CardRevealToken)
        .where(
            CardRevealToken.token_hash == token_hash,
            CardRevealToken.used_at.is_(None),
            CardRevealToken.expires_at > now,
            exists(select(VirtualCard.id).where(card_match)),
        )
        .values(used_at=now)
        .returning(CardRevealToken.card_id)
        .execution_options(synchronize_session=False)
    )
    if organization_id is not None:
        claim = claim.where(CardRevealToken.organization_id == organization_id)

    card_id = (await db.execute(claim)).scalar_one_or_none()
    if card_id is None:
        return None, await _classify_claim_failure(db, token_hash, organization_id, now)

    card_result = await db.execute(select(VirtualCard).where(VirtualCard.id == card_id))
    card = card_result.scalar_one_or_none()
    if card is None:  # pragma: no cover — the claim's EXISTS + the FK guarantee it
        return None, "invalid"
    return card, None
