"""GST computation tests — Australia, India (CGST/SGST/IGST), Canada."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.international_tax.gst import compute_gst


def test_australia_single_gst():
    r = compute_gst(net_amount=Decimal("1000"), rate=Decimal("10"), country="AU")
    assert r.gst_amount == Decimal("100.00")
    assert r.gross_amount == Decimal("1100.00")
    assert r.components == {"gst": Decimal("100.00")}


def test_india_intrastate_splits_cgst_sgst():
    # 18% on 1000 = 180 total, split 9% CGST + 9% SGST.
    r = compute_gst(net_amount=Decimal("1000"), rate=Decimal("18"), country="IN")
    assert r.gst_amount == Decimal("180.00")
    assert r.components["cgst"] == Decimal("90.00")
    assert r.components["sgst"] == Decimal("90.00")
    # Components always sum to the total tax.
    assert r.components["cgst"] + r.components["sgst"] == r.gst_amount


def test_india_interstate_uses_igst():
    r = compute_gst(net_amount=Decimal("1000"), rate=Decimal("18"), country="IN", interstate=True)
    assert r.components == {"igst": Decimal("180.00")}
    assert "cgst" not in r.components


def test_india_split_handles_odd_cent():
    # 18% on 999.99 = 179.9982 -> 180.00; halves must still reconcile.
    r = compute_gst(net_amount=Decimal("999.99"), rate=Decimal("18"), country="IN")
    assert r.components["cgst"] + r.components["sgst"] == r.gst_amount


def test_canada_federal_only():
    r = compute_gst(net_amount=Decimal("1000"), rate=Decimal("5"), country="CA")
    assert r.gst_amount == Decimal("50.00")
    assert r.components == {"gst": Decimal("50.00")}


def test_canada_with_provincial_component():
    # Federal 5% GST + 8% provincial PST on 1000.
    r = compute_gst(
        net_amount=Decimal("1000"),
        rate=Decimal("5"),
        country="CA",
        province_rate=Decimal("8"),
    )
    assert r.components["gst"] == Decimal("50.00")
    assert r.components["pst"] == Decimal("80.00")
    assert r.gst_amount == Decimal("130.00")
    assert r.gross_amount == Decimal("1130.00")


def test_all_amounts_are_decimal():
    r = compute_gst(net_amount=Decimal("100"), rate=Decimal("10"), country="AU")
    assert isinstance(r.gst_amount, Decimal)
    assert all(isinstance(v, Decimal) for v in r.components.values())


def test_vat_country_rejected_by_gst_path():
    with pytest.raises(ValueError):
        compute_gst(net_amount=Decimal("100"), rate=Decimal("20"), country="GB")
