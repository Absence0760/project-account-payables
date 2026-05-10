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
