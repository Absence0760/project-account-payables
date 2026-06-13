"""Country-specific tax validation — VAT / GST / IVA id formats, rate
plausibility, zero-rate / reverse-charge, unknown-country skip, and the PII
invariant (no value in any FieldError)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.e_invoice import validate_tax_id, validate_tax_rate
from app.services.e_invoice.model import (
    EInvoiceDocument,
    EInvoiceFormat,
    EInvoiceParty,
    EInvoiceTax,
)
from app.services.e_invoice.tax_rules import validate_tax_document


# ---------------------------------------------------------------------------
# Tax-ID format per country
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "country,tax_id",
    [
        ("DE", "DE123456789"),
        ("GB", "GB123456789"),
        ("GB", "GB999999973"),
        ("AU", "12345678901"),  # ABN — 11 digits
        ("NZ", "123456789"),  # GST / IRD
        ("IN", "29ABCDE1234F1Z5"),  # GSTIN
        ("CA", "123456789"),  # Business Number
        ("CA", "123456789RT0001"),  # with RT program account
        ("MX", "ABCD901231XYZ"),  # RFC (moral)
        ("ES", "ESA12345674"),  # IVA / NIF
        ("IT", "IT12345678901"),  # IVA Partita
        ("FR", "FR40123456789"),
        ("GR", "EL123456789"),  # Greece: VAT prefix is EL, not the ISO-2 GR
    ],
)
def test_valid_tax_ids_pass(country, tax_id):
    assert validate_tax_id(country, tax_id) is None


@pytest.mark.parametrize(
    "country,tax_id",
    [
        ("DE", "DE12"),  # too short
        ("GB", "123456789"),  # missing GB prefix
        ("AU", "12345"),  # not 11 digits
        ("NZ", "ABC"),
        ("IN", "29ABCDE"),  # truncated GSTIN
        ("CA", "12AB"),
        ("MX", "1"),
        ("ES", "ESZZ"),
        ("IT", "IT12"),
        ("GR", "GR123456789"),  # ISO-2 prefix, not the VAT prefix (EL) — the easy mistake
    ],
)
def test_malformed_tax_ids_flagged(country, tax_id):
    assert validate_tax_id(country, tax_id) == "malformed"


def test_unknown_country_is_skipped():
    # A regime we don't model — never reject, just skip.
    assert validate_tax_id("ZZ", "ANYTHING") is None
    assert validate_tax_id("US", "12-3456789") is None
    assert validate_tax_id(None, "12345") is None


def test_missing_tax_id_is_not_a_format_error():
    # Absence is a structural concern, not a format one.
    assert validate_tax_id("DE", None) is None


# ---------------------------------------------------------------------------
# Tax-rate plausibility
# ---------------------------------------------------------------------------
def test_german_standard_and_reduced_rates_plausible():
    assert validate_tax_rate("DE", Decimal("19.00")) is None
    assert validate_tax_rate("DE", Decimal("7.00")) is None


def test_implausible_rate_flagged():
    assert validate_tax_rate("DE", Decimal("95.00")) == "implausible"


def test_rate_tolerance_boundary():
    """_RATE_TOLERANCE is 0.01: a rate 0.01 off a known rate is a rounding
    artefact (passes); 0.02 off is over the line (implausible). Pin both sides
    of the boundary, not just exact-match and far-off."""
    assert validate_tax_rate("DE", Decimal("19.01")) is None  # within tolerance
    assert validate_tax_rate("DE", Decimal("19.02")) == "implausible"  # just outside


def test_zero_rate_category_plausible():
    assert validate_tax_rate("DE", Decimal("0.00"), category="Z") is None


def test_reverse_charge_category_plausible_at_zero():
    assert validate_tax_rate("DE", Decimal("0.00"), category="AE") is None
    assert validate_tax_rate("GB", Decimal("0.00"), category="E") is None


def test_nonzero_rate_with_reverse_charge_category_passes():
    """Intentional design (tax_rules.py): a zero-rate/reverse-charge/exempt
    category short-circuits plausibility for ANY rate, even a non-zero one — we
    don't second-guess the seller's stated category. Pin the branch so it can't
    silently change."""
    assert validate_tax_rate("DE", Decimal("19.00"), category="AE") is None
    assert validate_tax_rate("DE", Decimal("19.00"), category="Z") is None


def test_unknown_country_rate_skipped():
    assert validate_tax_rate("ZZ", Decimal("95.00")) is None
    assert validate_tax_rate(None, Decimal("95.00")) is None


def test_zero_rate_plausible_without_category():
    assert validate_tax_rate("AU", Decimal("0.00")) is None


# ---------------------------------------------------------------------------
# Document-level
# ---------------------------------------------------------------------------
def _doc(**kw) -> EInvoiceDocument:
    defaults = dict(
        source_format=EInvoiceFormat.UBL,
        seller=EInvoiceParty(name="S", tax_id="DE123456789", country_code="DE"),
        buyer=EInvoiceParty(name="B"),
        taxes=[EInvoiceTax(category="S", rate=Decimal("19.00"))],
    )
    defaults.update(kw)
    return EInvoiceDocument(**defaults)


def test_valid_document_has_no_tax_errors():
    assert validate_tax_document(_doc()) == []


def test_malformed_seller_tax_id_yields_field_error():
    doc = _doc(seller=EInvoiceParty(name="S", tax_id="DE12", country_code="DE"))
    errors = validate_tax_document(doc)
    codes = {e.field: e.code for e in errors}
    assert codes.get("seller.tax_id") == "malformed"


def test_malformed_buyer_tax_id_yields_field_error():
    doc = _doc(buyer=EInvoiceParty(name="B", tax_id="GB12", country_code="GB"))
    errors = validate_tax_document(doc)
    codes = {e.field: e.code for e in errors}
    assert codes.get("buyer.tax_id") == "malformed"


def test_implausible_rate_yields_indexed_field_error():
    doc = _doc(taxes=[EInvoiceTax(category="S", rate=Decimal("95.00"))])
    errors = validate_tax_document(doc)
    codes = {e.field: e.code for e in errors}
    assert codes.get("taxes[0].rate") == "implausible"


def test_no_seller_rate_errors_when_country_code_none():
    """When the seller has no country_code (the outbound mapper case — the
    Invoice row has no vendor-country when the VAT-id prefix is unrecognised),
    rate plausibility is skipped entirely: even a clearly implausible rate
    produces no error. This pins the intentional skip so an outbound rate bug
    isn't silently masked by a missing country."""
    doc = _doc(
        seller=EInvoiceParty(name="S", tax_id="123", country_code=None),
        taxes=[EInvoiceTax(category="S", rate=Decimal("95.00"))],
    )
    errors = validate_tax_document(doc)
    assert all(not e.field.startswith("taxes[") for e in errors)
    assert all(e.field != "seller.tax_id" for e in errors)


def test_pii_never_leaks_into_field_errors():
    """Critical: no tax-id / amount value may appear in any FieldError."""
    doc = EInvoiceDocument(
        source_format=EInvoiceFormat.UBL,
        seller=EInvoiceParty(name="S", tax_id="DE-SECRET-XX", country_code="DE"),
        buyer=EInvoiceParty(name="B", tax_id="GB-SECRET-YY", country_code="GB"),
        taxes=[EInvoiceTax(category="S", rate=Decimal("95.00"))],
    )
    errors = validate_tax_document(doc)
    assert errors
    forbidden = ["DE-SECRET-XX", "GB-SECRET-YY", "SECRET", "95.00", "95"]
    for err in errors:
        for needle in forbidden:
            assert needle not in err.message
            assert needle not in err.field
