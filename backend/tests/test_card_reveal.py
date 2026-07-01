"""Tests for the card-reveal single-use-token service.

Pure async service tests — no DB. The session is replaced with an
AsyncMock so we can assert the right rows are added / mutated without a
real Postgres connection.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def _card(**overrides):
    base = dict(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        vendor_id=uuid.uuid4(),
        last_four="1234",
        card_provider="mock",
        provider_card_id="mock_card_xyz",
        amount_limit=100,
        currency="USD",
        correlation_id=uuid.uuid4(),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _result_for(scalar=None):
    """Build the SQLAlchemy result object .execute() should return."""
    r = MagicMock()
    r.scalar_one_or_none = MagicMock(return_value=scalar)
    return r


@pytest.mark.asyncio
async def test_mint_token_persists_hashed_token_and_returns_plaintext():
    from app.services.card_reveal import _hash, mint_reveal_token

    db = AsyncMock()
    db.add = MagicMock()

    card = _card()
    plaintext = await mint_reveal_token(db, card)

    assert isinstance(plaintext, str)
    assert len(plaintext) >= 32  # secrets.token_urlsafe(32) → ~43 chars

    persisted = db.add.call_args.args[0]
    # The hash on the row matches the plaintext we returned.
    assert persisted.token_hash == _hash(plaintext)
    # Card + org are scoped.
    assert persisted.card_id == card.id
    assert persisted.organization_id == card.organization_id
    # Default expiry is 7 days from now.
    delta = persisted.expires_at - datetime.now(UTC)
    assert timedelta(days=6, hours=23) < delta < timedelta(days=7, hours=1)


@pytest.mark.asyncio
async def test_consume_returns_invalid_for_unknown_token():
    from app.services.card_reveal import consume_reveal_token

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_result_for(scalar=None))

    card, error = await consume_reveal_token(db, "nope")
    assert card is None
    assert error == "invalid"


@pytest.mark.asyncio
async def test_consume_returns_expired_when_past_expires_at():
    from app.services.card_reveal import consume_reveal_token

    row = SimpleNamespace(
        token_hash="x",
        card_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
        used_at=None,
    )
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_result_for(scalar=row))

    card, error = await consume_reveal_token(db, "anything")
    assert card is None
    assert error == "expired"


@pytest.mark.asyncio
async def test_consume_returns_used_when_already_consumed():
    from app.services.card_reveal import consume_reveal_token

    row = SimpleNamespace(
        token_hash="x",
        card_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        expires_at=datetime.now(UTC) + timedelta(days=1),
        used_at=datetime.now(UTC) - timedelta(hours=1),
    )
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_result_for(scalar=row))

    card, error = await consume_reveal_token(db, "anything")
    assert card is None
    assert error == "used"


@pytest.mark.asyncio
async def test_consume_returns_card_and_marks_used_on_first_call():
    from app.services.card_reveal import consume_reveal_token

    row = SimpleNamespace(
        token_hash="x",
        card_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        expires_at=datetime.now(UTC) + timedelta(days=1),
        used_at=None,
    )
    card_obj = _card(id=row.card_id)

    db = AsyncMock()
    # First execute: token lookup. Second execute: card lookup.
    db.execute = AsyncMock(side_effect=[_result_for(scalar=row), _result_for(scalar=card_obj)])

    card, error = await consume_reveal_token(db, "anything")
    assert error is None
    assert card is card_obj
    # Single-use semantics: row.used_at flips to a recent timestamp.
    assert row.used_at is not None
    assert (datetime.now(UTC) - row.used_at) < timedelta(seconds=5)


@pytest.mark.asyncio
async def test_consume_refuses_card_belonging_to_another_org():
    """Defense-in-depth org binding: the token row resolves (its own
    organization_id matches the caller's), but the card it points at carries a
    DIFFERENT organization_id. The reveal must be refused as opaque `invalid`
    and the token must NOT be marked used (nothing burned on a rejected reveal)."""
    from app.services.card_reveal import consume_reveal_token

    org_a = uuid.uuid4()
    org_b = uuid.uuid4()
    row = SimpleNamespace(
        token_hash="x",
        card_id=uuid.uuid4(),
        organization_id=org_a,
        expires_at=datetime.now(UTC) + timedelta(days=1),
        used_at=None,
    )
    # Card lives in a different org than the token/caller.
    card_obj = _card(id=row.card_id, organization_id=org_b)

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[_result_for(scalar=row), _result_for(scalar=card_obj)])

    card, error = await consume_reveal_token(db, "anything", organization_id=org_a)
    assert card is None
    assert error == "invalid"
    # The single-use token stays unburned on a rejected cross-org reveal.
    assert row.used_at is None


@pytest.mark.asyncio
async def test_consume_scopes_token_lookup_to_the_org(realdb):
    """A valid token consumed with the WRONG organization_id must not resolve —
    the token-row query itself is org-scoped, so a token can't be pivoted onto
    another tenant even if the (impossible) wrong DB were queried."""
    from app.services.card_reveal import consume_reveal_token

    mk = realdb.sessionmaker("a")
    org_a = realdb.info("a").org_id
    _card_id, token = await _seed_card_with_token(mk, org_a)

    # Wrong org id → invalid, and the token stays consumable.
    async with mk() as s:
        card_bad, error_bad = await consume_reveal_token(s, token, organization_id=uuid.uuid4())
        await s.commit()
    assert card_bad is None
    assert error_bad == "invalid"

    # The correct org id reveals it (proving the guard isn't failing-closed
    # for the wrong reason).
    async with mk() as s:
        card_ok, error_ok = await consume_reveal_token(s, token, organization_id=org_a)
        await s.commit()
    assert error_ok is None
    assert card_ok is not None


@pytest.mark.asyncio
async def test_reveal_token_not_burned_when_session_rolled_back(realdb):
    """The handler's outage path rolls back instead of committing so a transient
    provider failure doesn't permanently kill the single-use link. Simulate that
    at the service layer: consume (flips used_at in-session) then ROLL BACK — a
    fresh reveal afterwards must still succeed, because used_at was never
    persisted."""
    from app.services.card_reveal import consume_reveal_token

    mk = realdb.sessionmaker("a")
    org_a = realdb.info("a").org_id
    card_id, token = await _seed_card_with_token(mk, org_a)

    # First attempt: consume marks used_at, but we roll back (mimics the outage
    # branch that returns the fallback body without committing).
    async with mk() as s:
        card, error = await consume_reveal_token(s, token, organization_id=org_a)
        assert error is None
        await s.rollback()

    # Retry after "provider recovery": the token is still alive.
    async with mk() as s:
        card2, error2 = await consume_reveal_token(s, token, organization_id=org_a)
        await s.commit()
    assert error2 is None
    assert card2 is not None
    assert card2.id == card_id


# ---------------------------------------------------------------------------
# Real-Postgres: single-use survives a real commit + re-read, and the token
# is scoped to its own tenant DB (a token minted in A can't reveal in B).
# ---------------------------------------------------------------------------


async def _seed_card_with_token(mk, org_id):
    """Create an invoice + virtual card in a tenant DB and mint a reveal
    token, committing. Returns (card_id, plaintext_token)."""
    from decimal import Decimal

    from app.models.invoice import Invoice
    from app.models.virtual_card import VirtualCard
    from app.services.card_reveal import mint_reveal_token

    async with mk() as s:
        invoice = Invoice(
            organization_id=org_id,
            invoice_number="INV-CARD-1",
            vendor_name="Acme",
            amount=Decimal("100.00"),
        )
        s.add(invoice)
        await s.flush()
        card = VirtualCard(
            organization_id=org_id,
            invoice_id=invoice.id,
            card_provider="mock",
            provider_card_id="mock_card_x",
            amount_limit=Decimal("100.00"),
            last_four="4321",
        )
        s.add(card)
        await s.flush()
        token = await mint_reveal_token(s, card)
        card_id = card.id
        await s.commit()
    return card_id, token


@pytest.mark.asyncio
async def test_reveal_token_single_use_survives_commit(realdb):
    from app.services.card_reveal import consume_reveal_token

    mk = realdb.sessionmaker("a")
    card_id, token = await _seed_card_with_token(mk, realdb.info("a").org_id)

    # First reveal: returns the card and flips used_at — committed.
    async with mk() as s:
        card, error = await consume_reveal_token(s, token)
        await s.commit()
    assert error is None
    assert card.id == card_id

    # Second reveal in a fresh session: the committed used_at makes it dead.
    async with mk() as s:
        card2, error2 = await consume_reveal_token(s, token)
        await s.commit()
    assert card2 is None
    assert error2 == "used"


@pytest.mark.asyncio
async def test_reveal_token_is_scoped_to_its_tenant_db(realdb):
    """A token minted in tenant A's DB must not resolve against tenant B —
    the token_hash lookup runs on the per-tenant session, so cross-tenant
    PAN reveal is impossible."""
    from app.services.card_reveal import consume_reveal_token

    mk_a = realdb.sessionmaker("a")
    mk_b = realdb.sessionmaker("b")
    card_id, token = await _seed_card_with_token(mk_a, realdb.info("a").org_id)

    # Tenant B cannot see the token at all.
    async with mk_b() as s:
        card_b, error_b = await consume_reveal_token(s, token)
    assert card_b is None
    assert error_b == "invalid"

    # Tenant A (the owner) reveals it fine.
    async with mk_a() as s:
        card_a, error_a = await consume_reveal_token(s, token)
        await s.commit()
    assert error_a is None
    assert card_a.id == card_id
