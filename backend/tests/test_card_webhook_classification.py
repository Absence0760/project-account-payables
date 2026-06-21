"""Card webhook event classification + amount normalization.

Two bugs the pure helpers in `app.api.cards` close:

1. A naive substring match (`"auth" in event_type`) treated a *declined* or
   *reversed* authorization as a real charge — flipping the card to `charged`
   on money that never moved and minting a rebate on it.

2. Lithic webhook amounts are MINOR units (cents); Nium are MAJOR units. The
   handler divided BOTH by 100, recording 1/100th of every Nium charge.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.api.cards import _classify_card_event, _normalize_charge_amount

# ── Event classification ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "event_type",
    [
        "authorization",
        "authorization.created",
        "card.authorization",
        "auth.approved",
    ],
)
def test_genuine_authorizations_classify_as_charge(event_type):
    is_auth, is_settled = _classify_card_event(event_type)
    assert is_auth is True
    assert is_settled is False


@pytest.mark.parametrize(
    "event_type",
    [
        "authorization.decline",
        "authorization.declined",
        "authorization.reversal",
        "authorization.reversed",
        "transaction.voided",
        "transaction.refund",
        "transaction.return",
        "card.cancelled",
        "authorization.expired",
    ],
)
def test_declines_and_reversals_are_neither_charge_nor_settlement(event_type):
    # The core fix: a declined/reversed/voided event must NOT charge the card.
    is_auth, is_settled = _classify_card_event(event_type)
    assert is_auth is False, f"{event_type!r} must not be treated as a charge"
    assert is_settled is False, f"{event_type!r} must not be treated as a settlement"


@pytest.mark.parametrize(
    "event_type",
    ["transaction", "transaction.settled", "settlement.completed"],
)
def test_settlements_classify_as_settled(event_type):
    is_auth, is_settled = _classify_card_event(event_type)
    assert is_settled is True


def test_empty_event_type_is_inert():
    assert _classify_card_event("") == (False, False)
    assert _classify_card_event(None) == (False, False)


# ── Amount normalization (provider unit semantics) ────────────────────


def test_lithic_amount_is_minor_units_divided_by_100():
    # Lithic sends cents: 150000 == $1,500.00
    assert _normalize_charge_amount("lithic", 150000, Decimal("0")) == Decimal("1500.00")


def test_nium_amount_is_major_units_not_divided():
    # Nium sends major units: 50.00 == $50.00 (the bug recorded $0.50).
    assert _normalize_charge_amount("nium", "50.00", Decimal("0")) == Decimal("50.00")
    assert _normalize_charge_amount("nium", 50, Decimal("0")) == Decimal("50")


def test_falsy_amount_falls_back_to_card_limit():
    fallback = Decimal("100.00")
    assert _normalize_charge_amount("lithic", 0, fallback) == fallback
    assert _normalize_charge_amount("nium", None, fallback) == fallback


def test_unparseable_amount_falls_back():
    fallback = Decimal("100.00")
    assert _normalize_charge_amount("lithic", "not-a-number", fallback) == fallback


def test_normalized_amount_is_exact_decimal():
    # Money invariant: never float. A repeating value stays exact.
    result = _normalize_charge_amount("nium", "33.33", Decimal("0"))
    assert isinstance(result, Decimal)
    assert result == Decimal("33.33")
