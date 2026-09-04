"""`card_settlement_block` refuses an EXPIRED card.

Converging a payment onto a pre-existing card marks that payment `completed` —
an assertion that the money moved — and advances the invoice to `paid`. The
guard therefore has to be true of the card it converges onto.

It checked two things: the card is not already spent, and its limit covers the
payable. It did NOT check `expires_at`, so a card that had simply aged out was
a valid settlement target: the payment went `completed`, the invoice went
`paid`, and the vendor was never paid at all. That is worse than the
double-spend the spent-status check exists to prevent, because there is no
charge anywhere to reconcile against — the money simply never moved.

Reachable without any race, the same way the spent case is: mint a card for a
run → the payment is voided → the invoice returns to the payable pool → a run
weeks later rediscovers the card, now past its expiry.

Recorded as an unverified lead in `docs/followups.md`
("`card_settlement_block` may ignore `expires_at`"); confirmed here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.card_issuance import card_settlement_block

_NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def _card(*, status="active", limit="1000.00", expires_at=None):
    return SimpleNamespace(
        status=status,
        amount_limit=Decimal(limit),
        expires_at=expires_at,
    )


def test_live_unexpired_card_settles():
    card = _card(expires_at=_NOW + timedelta(days=30))
    assert card_settlement_block(card, Decimal("500.00"), now=_NOW) is None


def test_expired_card_is_refused():
    card = _card(expires_at=_NOW - timedelta(days=1))
    assert card_settlement_block(card, Decimal("500.00"), now=_NOW) == "card_expired"


def test_expiry_boundary_is_inclusive():
    """A card expiring exactly now can no longer be charged, so it cannot be
    what settled the payment."""
    card = _card(expires_at=_NOW)
    assert card_settlement_block(card, Decimal("500.00"), now=_NOW) == "card_expired"


def test_one_second_before_expiry_still_settles():
    card = _card(expires_at=_NOW + timedelta(seconds=1))
    assert card_settlement_block(card, Decimal("500.00"), now=_NOW) is None


def test_card_without_an_expiry_is_treated_as_non_expiring():
    """`expires_at` is nullable and several providers do not return one.
    Refusing on a missing value would block every such card."""
    card = _card(expires_at=None)
    assert card_settlement_block(card, Decimal("500.00"), now=_NOW) is None


def test_naive_expiry_is_compared_as_utc_rather_than_raising():
    """The column is timezone-aware, but a row built by an adapter that dropped
    tzinfo must not raise inside the money path."""
    naive_past = (_NOW - timedelta(days=1)).replace(tzinfo=None)
    assert card_settlement_block(_card(expires_at=naive_past), Decimal("1.00"), now=_NOW) == (
        "card_expired"
    )

    naive_future = (_NOW + timedelta(days=1)).replace(tzinfo=None)
    assert card_settlement_block(_card(expires_at=naive_future), Decimal("1.00"), now=_NOW) is None


@pytest.mark.parametrize("spent_status", ["charged", "completed"])
def test_spent_status_still_wins_over_expiry(spent_status):
    """Ordering matters for the operator-facing reason: a spent card that also
    expired is reported as spent, which is the more actionable fact (the money
    did move, and against which payment)."""
    card = _card(status=spent_status, expires_at=_NOW - timedelta(days=1))
    assert card_settlement_block(card, Decimal("1.00"), now=_NOW) == "card_already_charged"


def test_expiry_is_checked_before_the_limit():
    """An expired card that is also too small reports the expiry: raising the
    limit would not make it settleable, so the limit is the wrong advice."""
    card = _card(limit="1.00", expires_at=_NOW - timedelta(days=1))
    assert card_settlement_block(card, Decimal("500.00"), now=_NOW) == "card_expired"


def test_limit_check_survives_for_a_live_card():
    card = _card(limit="100.00", expires_at=_NOW + timedelta(days=30))
    assert card_settlement_block(card, Decimal("500.00"), now=_NOW) == (
        "card_already_issued_insufficient_limit"
    )


def test_now_defaults_to_the_current_clock():
    """The injectable `now` is a testing seam, not a required argument — the
    money path calls this with one argument."""
    assert card_settlement_block(_card(expires_at=None), Decimal("1.00")) is None
    long_past = datetime(2000, 1, 1, tzinfo=UTC)
    assert card_settlement_block(_card(expires_at=long_past), Decimal("1.00")) == "card_expired"


def test_reason_is_pii_free():
    """Returned strings become `Payment.failure_reason`, which is
    operator-facing — no PAN, no last four."""
    card = _card(expires_at=_NOW - timedelta(days=1))
    reason = card_settlement_block(card, Decimal("1.00"), now=_NOW)
    assert reason == "card_expired"
    assert reason.replace("_", "").isalpha()
