"""Withholding-tax computation tests — by jurisdiction + category + treaty."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.international_tax.country_rules import UnknownCountry
from app.services.international_tax.withholding import compute_withholding


def test_uk_services_withholding():
    r = compute_withholding(
        gross_amount=Decimal("1000"), supplier_country="GB", category="services"
    )
    assert r.withholding_rate == Decimal("20")
    assert r.withholding_amount == Decimal("200.00")
    assert r.net_payable == Decimal("800.00")
    assert isinstance(r.withholding_amount, Decimal)


def test_default_bracket_when_category_unmatched():
    # GB default bracket is 0% -> nothing withheld.
    r = compute_withholding(
        gross_amount=Decimal("1000"), supplier_country="GB", category="unmatched_cat"
    )
    assert r.withholding_rate == Decimal("0")
    assert r.withholding_amount == Decimal("0.00")
    assert r.net_payable == Decimal("1000.00")


def test_australia_no_abn_withholding():
    # No-ABN suppliers face 47% withholding.
    r = compute_withholding(gross_amount=Decimal("1000"), supplier_country="AU", category="no_abn")
    assert r.withholding_rate == Decimal("47")
    assert r.withholding_amount == Decimal("470.00")
    assert r.net_payable == Decimal("530.00")


def test_india_tds_professional_services():
    r = compute_withholding(
        gross_amount=Decimal("1000"),
        supplier_country="IN",
        category="professional_services",
    )
    assert r.withholding_rate == Decimal("10")
    assert r.withholding_amount == Decimal("100.00")


def test_treaty_rate_lowers_statutory():
    # ZA royalties statutory 15%; treaty 5% applies.
    r = compute_withholding(
        gross_amount=Decimal("1000"),
        supplier_country="ZA",
        category="royalties",
        treaty_rate=Decimal("5"),
    )
    assert r.treaty_applied is True
    assert r.withholding_rate == Decimal("5")
    assert r.withholding_amount == Decimal("50.00")


def test_treaty_rate_higher_than_statutory_is_ignored():
    # A treaty can reduce, not raise — a 25% "treaty" never beats 15% statutory.
    r = compute_withholding(
        gross_amount=Decimal("1000"),
        supplier_country="ZA",
        category="royalties",
        treaty_rate=Decimal("25"),
    )
    assert r.treaty_applied is False
    assert r.withholding_rate == Decimal("15")


def test_rounding_half_up():
    # 10% of 999.95 = 99.995 -> 100.00 (ROUND_HALF_UP)
    r = compute_withholding(
        gross_amount=Decimal("999.95"),
        supplier_country="IN",
        category="professional_services",
    )
    assert r.withholding_amount == Decimal("100.00")
    assert r.net_payable == Decimal("899.95")


def test_unknown_country_raises():
    with pytest.raises(UnknownCountry):
        compute_withholding(gross_amount=Decimal("100"), supplier_country="ZZ")
