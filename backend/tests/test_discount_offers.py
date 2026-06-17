"""Unit tests for ``app.services.discount_offers`` — pure, no DB.

Lifecycle helpers mutate plain ``SimpleNamespace`` stand-ins for the
``DiscountOffer`` ORM row, so these run in isolation while sibling workers
build the router / migration concurrently.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models.discount import (
    OFFER_SCOPE_VENDOR,
    OFFER_SOURCE_SUPPLIER,
    OFFER_STATUS_ACCEPTED,
    OFFER_STATUS_CAPTURED,
    OFFER_STATUS_DECLINED,
    OFFER_STATUS_EXPIRED,
    OFFER_STATUS_OFFERED,
)
from app.services import discount_offers as do


def _offer(**overrides) -> SimpleNamespace:
    base = dict(
        status=OFFER_STATUS_OFFERED,
        accepted_tier=None,
        accepted_at=None,
        accepted_by=None,
        captured_amount=None,
        captured_at=None,
        valid_until=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# --------------------------------------------------------------------------- #
# Tier normalization
# --------------------------------------------------------------------------- #


def test_normalize_tier_coerces_percent_to_string():
    t = do.normalize_tier({"days": 5, "percent": 3})
    assert t == {"days": 5, "percent": "3.00"}
    assert isinstance(t["percent"], str)


def test_normalize_tier_quantizes_to_two_dp():
    assert do.normalize_tier({"days": 10, "percent": "2.5"})["percent"] == "2.50"
    # ROUND_HALF_UP: 1.005 → 1.01
    assert do.normalize_tier({"days": 10, "percent": "1.005"})["percent"] == "1.01"


@pytest.mark.parametrize(
    "bad",
    [
        {"days": -1, "percent": "2.00"},
        {"days": 5, "percent": "0"},
        {"days": 5, "percent": "100"},
        {"days": 5, "percent": "150"},
        {"days": 5},
        {"percent": "2.00"},
        {"days": "abc", "percent": "2.00"},
        {"days": 5, "percent": "nope"},
    ],
)
def test_normalize_tier_rejects_malformed(bad):
    with pytest.raises(ValueError):
        do.normalize_tier(bad)


def test_parse_tiers_sorts_ascending_by_days():
    tiers = do.parse_tiers(
        [{"days": 15, "percent": "1"}, {"days": 5, "percent": "3"}, {"days": 10, "percent": "2"}]
    )
    assert [t["days"] for t in tiers] == [5, 10, 15]
    assert [t["percent"] for t in tiers] == ["3.00", "2.00", "1.00"]


def test_parse_tiers_rejects_empty():
    with pytest.raises(ValueError):
        do.parse_tiers([])


def test_parse_tiers_rejects_duplicate_days():
    with pytest.raises(ValueError):
        do.parse_tiers([{"days": 5, "percent": "3"}, {"days": 5, "percent": "2"}])


# --------------------------------------------------------------------------- #
# Tier selection
# --------------------------------------------------------------------------- #


def test_select_tier_exact_match_and_miss():
    tiers = do.parse_tiers([{"days": 5, "percent": "3"}, {"days": 10, "percent": "2"}])
    assert do.select_tier(tiers, 10) == {"days": 10, "percent": "2.00"}
    assert do.select_tier(tiers, 7) is None


def test_best_tier_picks_highest_percent_when_all_open():
    # reference defaults to as_of, so on day 0 every rung is still open ⇒ best %.
    tiers = do.parse_tiers(
        [{"days": 5, "percent": "3"}, {"days": 10, "percent": "2"}, {"days": 15, "percent": "1"}]
    )
    best = do.best_tier_for_date(tiers, as_of=date(2026, 1, 1), valid_until=None)
    assert best == {"days": 5, "percent": "3.00"}


def test_best_tier_window_measured_from_reference_date():
    tiers = do.parse_tiers([{"days": 5, "percent": "3"}, {"days": 10, "percent": "2"}])
    ref = date(2026, 1, 1)
    # Day 6: the 5-day rung's deadline (Jan 6) is exactly today → still open.
    assert do.best_tier_for_date(
        tiers, as_of=date(2026, 1, 6), valid_until=None, reference_date=ref
    ) == {"days": 5, "percent": "3.00"}
    # Day 7: 5-day rung closed (deadline Jan 6 < Jan 7); best remaining is 10-day 2%.
    assert do.best_tier_for_date(
        tiers, as_of=date(2026, 1, 7), valid_until=None, reference_date=ref
    ) == {"days": 10, "percent": "2.00"}
    # Day 11: 10-day rung's deadline (Jan 11) is exactly today → still open.
    assert do.best_tier_for_date(
        tiers, as_of=date(2026, 1, 11), valid_until=None, reference_date=ref
    ) == {"days": 10, "percent": "2.00"}
    # Day 12: every rung closed.
    assert (
        do.best_tier_for_date(
            tiers, as_of=date(2026, 1, 12), valid_until=None, reference_date=ref
        )
        is None
    )


def test_best_tier_none_when_offer_window_passed():
    tiers = do.parse_tiers([{"days": 5, "percent": "3"}])
    assert (
        do.best_tier_for_date(
            tiers, as_of=date(2026, 2, 1), valid_until=date(2026, 1, 31)
        )
        is None
    )


# --------------------------------------------------------------------------- #
# Savings math
# --------------------------------------------------------------------------- #


def test_discount_savings_exact_cents():
    tier = {"days": 5, "percent": "3.00"}
    assert do.discount_savings(Decimal("1000.00"), tier) == Decimal("30.00")


def test_discount_savings_rounds_half_up():
    # 333.33 * 1.5% = 4.99995 → 5.00
    tier = {"days": 10, "percent": "1.50"}
    assert do.discount_savings(Decimal("333.33"), tier) == Decimal("5.00")


# --------------------------------------------------------------------------- #
# Lifecycle transitions
# --------------------------------------------------------------------------- #


def test_accept_offer_sets_fields():
    offer = _offer()
    actor = uuid.uuid4()
    now = datetime(2026, 1, 2, tzinfo=UTC)
    do.accept_offer(offer, tier={"days": 5, "percent": 3}, actor_id=actor, now=now)
    assert offer.status == OFFER_STATUS_ACCEPTED
    assert offer.accepted_tier == {"days": 5, "percent": "3.00"}
    assert offer.accepted_at == now
    assert offer.accepted_by == actor


def test_accept_offer_guard_rejects_non_offered():
    offer = _offer(status=OFFER_STATUS_ACCEPTED)
    with pytest.raises(ValueError):
        do.accept_offer(
            offer, tier={"days": 5, "percent": 3}, actor_id=uuid.uuid4(), now=datetime.now(UTC)
        )


def test_decline_offer():
    offer = _offer()
    do.decline_offer(offer, now=datetime.now(UTC))
    assert offer.status == OFFER_STATUS_DECLINED


def test_decline_offer_guard():
    offer = _offer(status=OFFER_STATUS_CAPTURED)
    with pytest.raises(ValueError):
        do.decline_offer(offer, now=datetime.now(UTC))


def test_mark_captured_from_accepted():
    offer = _offer(status=OFFER_STATUS_ACCEPTED)
    now = datetime(2026, 1, 3, tzinfo=UTC)
    do.mark_captured(offer, captured_amount=Decimal("30.005"), now=now)
    assert offer.status == OFFER_STATUS_CAPTURED
    assert offer.captured_amount == Decimal("30.01")  # quantized half-up
    assert offer.captured_at == now


def test_mark_captured_guard_requires_accepted():
    offer = _offer()  # still 'offered'
    with pytest.raises(ValueError):
        do.mark_captured(offer, captured_amount=Decimal("10.00"), now=datetime.now(UTC))


def test_expire_if_past_changes_when_window_closed():
    offer = _offer(valid_until=date(2026, 1, 1))
    assert do.expire_if_past(offer, as_of=date(2026, 1, 2)) is True
    assert offer.status == OFFER_STATUS_EXPIRED


def test_expire_if_past_no_change_in_window():
    offer = _offer(valid_until=date(2026, 1, 10))
    assert do.expire_if_past(offer, as_of=date(2026, 1, 5)) is False
    assert offer.status == OFFER_STATUS_OFFERED


def test_expire_if_past_no_change_without_valid_until():
    offer = _offer(valid_until=None)
    assert do.expire_if_past(offer, as_of=date(2026, 1, 5)) is False


def test_expire_if_past_no_change_when_already_accepted():
    offer = _offer(status=OFFER_STATUS_ACCEPTED, valid_until=date(2026, 1, 1))
    assert do.expire_if_past(offer, as_of=date(2026, 2, 1)) is False
    assert offer.status == OFFER_STATUS_ACCEPTED


# --------------------------------------------------------------------------- #
# Bulk vendor negotiation
# --------------------------------------------------------------------------- #


def test_build_bulk_offer_sums_base_amount():
    vendor_id = uuid.uuid4()
    bulk = do.build_bulk_offer(
        vendor_id=vendor_id,
        open_amounts=[Decimal("100.10"), Decimal("250.20"), Decimal("49.70")],
        tiers=[{"days": 10, "percent": "2"}, {"days": 5, "percent": "3"}],
        valid_until=date(2026, 3, 1),
        notes="Q1 bulk",
    )
    assert bulk.scope == OFFER_SCOPE_VENDOR
    assert bulk.source == OFFER_SOURCE_SUPPLIER
    assert bulk.vendor_id == vendor_id
    assert bulk.base_amount == Decimal("400.00")
    assert bulk.invoice_count == 3
    # tiers normalized + sorted ascending
    assert [t["days"] for t in bulk.tiers] == [5, 10]
    kwargs = bulk.as_offer_kwargs()
    assert kwargs["base_amount"] == Decimal("400.00")
    assert kwargs["vendor_id"] == vendor_id
    assert "invoice_count" not in kwargs  # not an ORM column


def test_build_bulk_offer_rejects_empty():
    with pytest.raises(ValueError):
        do.build_bulk_offer(
            vendor_id=uuid.uuid4(), open_amounts=[], tiers=[{"days": 5, "percent": "3"}]
        )


def test_build_bulk_offer_rejects_nonpositive_base():
    with pytest.raises(ValueError):
        do.build_bulk_offer(
            vendor_id=uuid.uuid4(),
            open_amounts=[Decimal("0.00")],
            tiers=[{"days": 5, "percent": "3"}],
        )
