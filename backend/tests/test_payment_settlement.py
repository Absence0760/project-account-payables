"""Unit tests for the pure settlement-amount verifier.

The processor webhook is the settlement moment for every real rail. Before
`services/payment_settlement.py` existed nothing compared the amount the
processor said it moved against the amount AP authorized — a `completed`
event was taken at face value, the discount was captured off OUR number, and
the ERP was told the invoice was paid.

These tests pin the verdict table: exact match, cent tolerance, over- and
under-settlement, the FX two-leg case, an unauthorized currency, and the
fail-open `unverified` branch for an adapter whose payload carries no amount.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.payment_settlement import (
    COVERAGE_COVERED,
    COVERAGE_SHORT,
    COVERAGE_UNCERTAIN,
    OUTCOME_AMOUNT_MISMATCH,
    OUTCOME_CURRENCY_MISMATCH,
    OUTCOME_MATCHED,
    OUTCOME_UNVERIFIED,
    REASON_NO_SETTLED_AMOUNT,
    SETTLEMENT_AMOUNT_TOLERANCE,
    build_authorized_legs,
    describe_discrepancy,
    settlement_coverage,
    verify_settlement,
)

# ---------------------------------------------------------------------------
# The ordinary domestic path
# ---------------------------------------------------------------------------


def test_exact_amount_and_currency_matches():
    v = verify_settlement(
        reported_amount=Decimal("1234.56"),
        reported_currency="USD",
        target_amount=Decimal("1234.56"),
        target_currency="USD",
    )
    assert v.outcome == OUTCOME_MATCHED
    assert v.is_discrepancy is False
    assert v.variance == Decimal("0.00")
    assert v.authorized_leg == "target"


def test_currency_comparison_is_case_and_whitespace_insensitive():
    v = verify_settlement(
        reported_amount=Decimal("10.00"),
        reported_currency=" usd ",
        target_amount=Decimal("10.00"),
        target_currency="USD",
    )
    assert v.outcome == OUTCOME_MATCHED


def test_one_cent_difference_is_within_tolerance():
    """The same one-cent band positive_pay and bank_reconciliation use —
    rounding between a processor's minor units and our Numeric(15,2) must
    not manufacture a fraud flag."""
    assert SETTLEMENT_AMOUNT_TOLERANCE == Decimal("0.01")
    v = verify_settlement(
        reported_amount=Decimal("100.01"),
        reported_currency="USD",
        target_amount=Decimal("100.00"),
        target_currency="USD",
    )
    assert v.outcome == OUTCOME_MATCHED
    assert v.variance == Decimal("0.01")


def test_two_cent_difference_is_a_mismatch():
    v = verify_settlement(
        reported_amount=Decimal("100.02"),
        reported_currency="USD",
        target_amount=Decimal("100.00"),
        target_currency="USD",
    )
    assert v.outcome == OUTCOME_AMOUNT_MISMATCH
    assert v.is_discrepancy is True
    assert v.variance == Decimal("0.02")


# ---------------------------------------------------------------------------
# Direction of the variance — positive means the processor moved MORE
# ---------------------------------------------------------------------------


def test_over_settlement_reports_positive_variance():
    """A wire that left at $50,000 against a $5,000 instruction. Positive
    variance = the processor took more than we authorized (the same sign
    convention `bank_reconciliation.match_variance` uses)."""
    v = verify_settlement(
        reported_amount=Decimal("50000.00"),
        reported_currency="USD",
        target_amount=Decimal("5000.00"),
        target_currency="USD",
    )
    assert v.outcome == OUTCOME_AMOUNT_MISMATCH
    assert v.variance == Decimal("45000.00")
    assert v.settled_amount == Decimal("50000.00")
    assert v.authorized_amount == Decimal("5000.00")


def test_under_settlement_reports_negative_variance():
    """A partial settlement — the supplier got half. The invoice is NOT
    paid in full, and a positive-only check would miss it entirely."""
    v = verify_settlement(
        reported_amount=Decimal("250.00"),
        reported_currency="USD",
        target_amount=Decimal("500.00"),
        target_currency="USD",
    )
    assert v.outcome == OUTCOME_AMOUNT_MISMATCH
    assert v.variance == Decimal("-250.00")


# ---------------------------------------------------------------------------
# Cross-currency: EITHER authorized leg is a legitimate report
# ---------------------------------------------------------------------------


def test_source_leg_amount_matches_for_an_fx_payment():
    """A EUR invoice on a USD-home org debits `source_amount` USD and credits
    `amount` EUR. A processor reporting the USD debit must not be flagged."""
    v = verify_settlement(
        reported_amount=Decimal("1086.96"),
        reported_currency="USD",
        target_amount=Decimal("1000.00"),
        target_currency="EUR",
        source_amount=Decimal("1086.96"),
        source_currency="USD",
    )
    assert v.outcome == OUTCOME_MATCHED
    assert v.authorized_leg == "source"


def test_target_leg_amount_matches_for_an_fx_payment():
    v = verify_settlement(
        reported_amount=Decimal("1000.00"),
        reported_currency="EUR",
        target_amount=Decimal("1000.00"),
        target_currency="EUR",
        source_amount=Decimal("1086.96"),
        source_currency="USD",
    )
    assert v.outcome == OUTCOME_MATCHED
    assert v.authorized_leg == "target"


def test_third_amount_on_an_fx_payment_is_a_mismatch_against_the_closest_leg():
    v = verify_settlement(
        reported_amount=Decimal("1200.00"),
        reported_currency="USD",
        target_amount=Decimal("1000.00"),
        target_currency="EUR",
        source_amount=Decimal("1086.96"),
        source_currency="USD",
    )
    assert v.outcome == OUTCOME_AMOUNT_MISMATCH
    # Only the USD (source) leg is currency-compatible, so that's the comparison.
    assert v.authorized_leg == "source"
    assert v.variance == Decimal("113.04")


def test_equal_legs_collapse_to_one():
    """A domestic payment still gets `source_currency`/`source_amount`
    stamped by `prepare_international_payment` when the corridor runs. The
    duplicate leg must not appear twice, so the verdict names one leg."""
    legs = build_authorized_legs(
        target_amount=Decimal("100.00"),
        target_currency="USD",
        source_amount=Decimal("100.00"),
        source_currency="usd",
    )
    assert len(legs) == 1
    assert legs[0].leg == "target"


def test_source_leg_present_when_amounts_differ():
    legs = build_authorized_legs(
        target_amount=Decimal("1000.00"),
        target_currency="EUR",
        source_amount=Decimal("1086.96"),
        source_currency="USD",
    )
    assert [leg.leg for leg in legs] == ["target", "source"]


# ---------------------------------------------------------------------------
# Currency the payment never authorized
# ---------------------------------------------------------------------------


def test_unauthorized_currency_is_a_currency_mismatch_not_a_numeric_match():
    """1000 EUR and 1000 USD are the same NUMBER and wildly different money.
    An amount-only check would call this settled."""
    v = verify_settlement(
        reported_amount=Decimal("1000.00"),
        reported_currency="EUR",
        target_amount=Decimal("1000.00"),
        target_currency="USD",
    )
    assert v.outcome == OUTCOME_CURRENCY_MISMATCH
    assert v.is_discrepancy is True
    # No numeric comparison is meaningful across currencies.
    assert v.variance is None
    assert v.settled_currency == "EUR"
    assert v.authorized_currency == "USD"


def test_unknown_authorized_currency_never_manufactures_a_mismatch():
    """`target_currency=None` means the invoice row could not be read, not
    that the currency is wrong. Missing data must not become evidence."""
    v = verify_settlement(
        reported_amount=Decimal("42.00"),
        reported_currency="ZAR",
        target_amount=Decimal("42.00"),
        target_currency=None,
    )
    assert v.outcome == OUTCOME_MATCHED


def test_provider_omitting_currency_still_compares_the_amount():
    v = verify_settlement(
        reported_amount=Decimal("99.99"),
        reported_currency=None,
        target_amount=Decimal("10.00"),
        target_currency="USD",
    )
    assert v.outcome == OUTCOME_AMOUNT_MISMATCH
    assert v.variance == Decimal("89.99")


# ---------------------------------------------------------------------------
# Fail-open: no reported amount is not evidence of a discrepancy
# ---------------------------------------------------------------------------


def test_missing_reported_amount_is_unverified_not_a_discrepancy():
    v = verify_settlement(
        reported_amount=None,
        reported_currency=None,
        target_amount=Decimal("500.00"),
        target_currency="USD",
    )
    assert v.outcome == OUTCOME_UNVERIFIED
    assert v.is_discrepancy is False
    assert v.variance is None
    # Still records what WAS authorized so the blind spot is visible on the
    # audit row rather than silent.
    assert v.authorized_amount == Decimal("500.00")


# ---------------------------------------------------------------------------
# Serialization for the audit row (project invariant: money as exact string)
# ---------------------------------------------------------------------------


def test_as_details_serialises_money_as_exact_strings():
    v = verify_settlement(
        reported_amount=Decimal("50000.00"),
        reported_currency="USD",
        target_amount=Decimal("5000.00"),
        target_currency="USD",
    )
    details = v.as_details()
    assert details["settled_amount"] == "50000.00"
    assert details["authorized_amount"] == "5000.00"
    assert details["variance"] == "45000.00"
    assert details["outcome"] == OUTCOME_AMOUNT_MISMATCH
    for value in details.values():
        assert not isinstance(value, float), "money must never serialise as float"


def test_as_details_is_json_safe_when_nothing_was_reported():
    v = verify_settlement(
        reported_amount=None,
        reported_currency=None,
        target_amount=Decimal("1.00"),
        target_currency="USD",
    )
    details = v.as_details()
    assert details["settled_amount"] is None
    assert details["variance"] is None


# ---------------------------------------------------------------------------
# The human-readable description that lands on the Exception row
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reported", "expect_word"),
    [(Decimal("600.00"), "MORE than"), (Decimal("400.00"), "LESS than")],
)
def test_describe_discrepancy_names_the_direction(reported, expect_word):
    v = verify_settlement(
        reported_amount=reported,
        reported_currency="USD",
        target_amount=Decimal("500.00"),
        target_currency="USD",
    )
    text = describe_discrepancy(v)
    assert expect_word in text
    assert "500.00 USD" in text


def test_describe_discrepancy_covers_the_currency_case():
    v = verify_settlement(
        reported_amount=Decimal("500.00"),
        reported_currency="EUR",
        target_amount=Decimal("500.00"),
        target_currency="USD",
    )
    text = describe_discrepancy(v)
    assert "currency mismatch" in text.lower()
    assert "EUR" in text and "USD" in text


def test_description_carries_no_pii():
    """The Exception row already holds the invoice FK — the description must
    stay to amounts + currency codes (PII-out-of-error-bodies invariant)."""
    v = verify_settlement(
        reported_amount=Decimal("2.00"),
        reported_currency="USD",
        target_amount=Decimal("1.00"),
        target_currency="USD",
    )
    text = describe_discrepancy(v).lower()
    for banned in ("iban", "account", "routing", "tax", "swift"):
        assert banned not in text


# ---------------------------------------------------------------------------
# Coverage — does the recorded settlement discharge the invoice?
# ---------------------------------------------------------------------------
#
# Distinct from the verdict above. `verify_settlement` answers "did the rail
# report what we authorized?"; `settlement_coverage` answers "may the invoice
# be marked paid?". They disagree on over-settlement, and that disagreement is
# the point: too much moving is worth flagging but does not leave the vendor
# short.


def test_exact_settlement_covers():
    c = settlement_coverage(
        settled_amount=Decimal("500.00"),
        settled_currency="USD",
        target_amount=Decimal("500.00"),
        target_currency="USD",
    )
    assert c.state == COVERAGE_COVERED
    assert c.completes_invoice is True
    assert c.shortfall is None


def test_under_settlement_is_short_with_the_gap():
    """The case the whole feature exists for: $250 moved against a $500
    instruction must NOT let the invoice read as settled in full."""
    c = settlement_coverage(
        settled_amount=Decimal("250.00"),
        settled_currency="USD",
        target_amount=Decimal("500.00"),
        target_currency="USD",
    )
    assert c.state == COVERAGE_SHORT
    assert c.completes_invoice is False
    assert c.shortfall == Decimal("250.00")


def test_over_settlement_still_covers():
    """Over-settlement is flagged by the verifier but discharges the payable —
    the vendor is not short, so holding the invoice would strand it for no
    protective reason."""
    c = settlement_coverage(
        settled_amount=Decimal("750.00"),
        settled_currency="USD",
        target_amount=Decimal("500.00"),
        target_currency="USD",
    )
    assert c.completes_invoice is True
    # ...and the verifier still calls it a discrepancy on the same numbers.
    assert verify_settlement(
        reported_amount=Decimal("750.00"),
        reported_currency="USD",
        target_amount=Decimal("500.00"),
        target_currency="USD",
    ).is_discrepancy


def test_unreported_settlement_fails_open():
    """NULL settled_amount must NOT hold the invoice.

    This is the property that keeps an amount-free rail (Dwolla's bare
    envelope) and every pre-0083 row from stranding. Absence is not evidence.
    """
    c = settlement_coverage(
        settled_amount=None,
        settled_currency=None,
        target_amount=Decimal("500.00"),
        target_currency="USD",
    )
    assert c.completes_invoice is True
    assert c.reason == REASON_NO_SETTLED_AMOUNT


def test_cent_shortfall_is_within_tolerance():
    """One cent is the band all three reconcilers share — a rounding artifact
    is not a shortfall."""
    c = settlement_coverage(
        settled_amount=Decimal("499.99"),
        settled_currency="USD",
        target_amount=Decimal("500.00"),
        target_currency="USD",
    )
    assert c.completes_invoice is True


def test_settling_the_source_leg_covers():
    """A cross-currency payment settles on either side; the processor picks.
    Reporting the source leg in the source currency is a full settlement."""
    c = settlement_coverage(
        settled_amount=Decimal("450.00"),
        settled_currency="EUR",
        target_amount=Decimal("500.00"),
        target_currency="USD",
        source_amount=Decimal("450.00"),
        source_currency="EUR",
    )
    assert c.completes_invoice is True


def test_short_against_every_leg_measures_the_smallest():
    """The shortfall claimed is the least defensible one — measured against
    the smallest authorized leg, not the largest."""
    c = settlement_coverage(
        settled_amount=Decimal("100.00"),
        settled_currency=None,  # wildcard: compatible with both legs
        target_amount=Decimal("500.00"),
        target_currency="USD",
        source_amount=Decimal("450.00"),
        source_currency="EUR",
    )
    assert c.state == COVERAGE_SHORT
    assert c.shortfall == Decimal("350.00")


def test_unauthorized_currency_is_uncertain_not_covered():
    """Money on a currency we never authorized cannot be called a settlement —
    comparing across currencies without a rate would invent an answer."""
    c = settlement_coverage(
        settled_amount=Decimal("500.00"),
        settled_currency="JPY",
        target_amount=Decimal("500.00"),
        target_currency="USD",
    )
    assert c.state == COVERAGE_UNCERTAIN
    assert c.completes_invoice is False
    assert c.shortfall is None
