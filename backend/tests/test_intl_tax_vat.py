"""VAT computation tests — standard, domestic, and EU reverse charge."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.international_tax.vat import compute_vat, validate_vat_number


def test_standard_vat_uk():
    r = compute_vat(net_amount=Decimal("1000"), rate=Decimal("20"), supplier_country="GB")
    assert r.vat_amount == Decimal("200.00")
    assert r.vat_payable == Decimal("200.00")
    assert r.gross_amount == Decimal("1200.00")
    assert r.reverse_charge is False
    # money is exact — Decimal throughout
    assert isinstance(r.vat_amount, Decimal)


def test_reduced_rate_rounds_half_up():
    # 5.5% of 999.99 = 54.99945 -> 55.00 (ROUND_HALF_UP)
    r = compute_vat(net_amount=Decimal("999.99"), rate=Decimal("5.5"), supplier_country="FR")
    assert r.vat_amount == Decimal("55.00")


def test_domestic_supply_is_not_reverse_charge():
    # Supplier and buyer both DE -> domestic, VAT charged normally.
    r = compute_vat(
        net_amount=Decimal("500"),
        rate=Decimal("19"),
        supplier_country="DE",
        buyer_country="DE",
        buyer_vat_registered=True,
    )
    assert r.reverse_charge is False
    assert r.vat_payable == Decimal("95.00")


def test_intra_eu_b2b_reverse_charge():
    # DE supplier -> FR VAT-registered buyer: reverse charge applies.
    r = compute_vat(
        net_amount=Decimal("1000"),
        rate=Decimal("19"),
        supplier_country="DE",
        buyer_country="FR",
        buyer_vat_registered=True,
    )
    assert r.reverse_charge is True
    # No cash VAT to the supplier...
    assert r.vat_payable == Decimal("0.00")
    assert r.gross_amount == Decimal("1000.00")
    # ...but the VAT is still reportable (buyer self-accounts).
    assert r.reportable_vat == Decimal("190.00")


def test_reverse_charge_needs_vat_registered_buyer():
    # Same corridor but buyer not VAT-registered -> normal VAT.
    r = compute_vat(
        net_amount=Decimal("1000"),
        rate=Decimal("19"),
        supplier_country="DE",
        buyer_country="FR",
        buyer_vat_registered=False,
    )
    assert r.reverse_charge is False
    assert r.vat_payable == Decimal("190.00")


def test_no_reverse_charge_when_buyer_outside_eu():
    # DE supplier -> US buyer: not intra-EU, no reverse charge here.
    r = compute_vat(
        net_amount=Decimal("1000"),
        rate=Decimal("19"),
        supplier_country="DE",
        buyer_country="US",
        buyer_vat_registered=True,
    )
    assert r.reverse_charge is False


def test_gst_country_rejected_by_vat_path():
    with pytest.raises(ValueError):
        compute_vat(net_amount=Decimal("100"), rate=Decimal("10"), supplier_country="AU")


def test_validate_vat_number():
    assert validate_vat_number("DE", "DE123456789") is True
    assert validate_vat_number("DE", "FR123456789") is False  # wrong prefix for EU
    assert validate_vat_number("DE", None) is False
    assert validate_vat_number("GB", "123456789") is True  # non-EU, no prefix required
