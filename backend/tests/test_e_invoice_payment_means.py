"""`cbc:PaymentMeansCode` / `ram:TypeCode` must carry a UNCL4461 CODE.

Regression: `mapper.invoice_to_einvoice_document` used to copy
`Invoice.payment_method` — our own `ach`/`wire`/`check`/`credit_card` dropdown
token — straight into `payment_means_code`, so every exported UBL and CII
document carried `<cbc:PaymentMeansCode>ach</cbc:PaymentMeansCode>`. That is not
a member of UN/EDIFACT code list 4461, and the proof it is unreadable is that
our OWN inbound adapter maps it back to `None`: an invoice we export and a
partner re-imports loses its payment means entirely.

Both directions now read one table (`e_invoice/payment_means.py`), so they
cannot drift.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.e_invoice.cii import parse_cii
from app.services.e_invoice.generate import generate_ubl
from app.services.e_invoice.generate_cii import generate_cii
from app.services.e_invoice.mapper import BuyerIdentity, invoice_to_einvoice_document
from app.services.e_invoice.payment_means import (
    METHOD_TO_PAYMENT_MEANS,
    PAYMENT_MEANS_TO_METHOD,
    method_to_payment_means,
    payment_means_to_method,
)
from app.services.e_invoice.ubl import parse_ubl


def _invoice(payment_method: str | None):
    return SimpleNamespace(
        invoice_number="INV-1",
        invoice_date=date(2024, 5, 1),
        due_date=date(2024, 6, 1),
        currency="EUR",
        reference_number=None,
        po_number=None,
        payment_terms="Net 30",
        payment_method=payment_method,
        vendor_name="Acme SARL",
        vendor_tax_id="FR40123456789",
        vendor_address="12 Rue de la Paix\nParis",
        subtotal=Decimal("1000.00"),
        amount=Decimal("1200.00"),
        tax_amount=Decimal("200.00"),
        tax_rate=Decimal("20.00"),
        discount_amount=None,
        shipping_amount=None,
    )


def _line():
    return SimpleNamespace(
        line_number=1,
        item_code="A1",
        description="Widget",
        quantity=Decimal("2"),
        unit_price=Decimal("500.00"),
        total=Decimal("1000.00"),
        tax=Decimal("200.00"),
    )


def _doc(payment_method: str | None):
    return invoice_to_einvoice_document(
        _invoice(payment_method), [_line()], BuyerIdentity(name="Globex Buyer Inc")
    )


# --- the table itself -------------------------------------------------------


@pytest.mark.parametrize("method", sorted(METHOD_TO_PAYMENT_MEANS))
def test_every_emitted_code_reads_back_as_the_same_method(method: str):
    code = method_to_payment_means(method)
    assert code is not None
    assert payment_means_to_method(code) == method


@pytest.mark.parametrize("code", sorted(PAYMENT_MEANS_TO_METHOD))
def test_every_known_code_passes_through_unchanged(code: str):
    """A row already holding a UNCL4461 code must not be re-encoded."""
    assert method_to_payment_means(code) == code


def test_unmappable_method_omits_the_element():
    # `other` has no honest UNCL4461 member; omitting the optional element is
    # valid, emitting an out-of-list code is not.
    assert method_to_payment_means("other") is None
    assert method_to_payment_means(None) is None
    assert _doc("other").payment_means_code is None


# --- through the generators -------------------------------------------------


def test_ubl_export_emits_a_code_our_own_reader_understands():
    doc = _doc("ach")
    assert doc.payment_means_code == "30"
    back = parse_ubl(generate_ubl(doc))
    assert back.payment_means_code == "30"
    assert payment_means_to_method(back.payment_means_code) == "ach"


def test_cii_export_emits_a_code_our_own_reader_understands():
    doc = _doc("check")
    assert doc.payment_means_code == "20"
    back = parse_cii(generate_cii(doc))
    assert back.payment_means_code == "20"
    assert payment_means_to_method(back.payment_means_code) == "check"


def test_no_internal_token_ever_reaches_the_wire():
    for method in ("ach", "wire", "check", "credit_card"):
        xml = generate_ubl(_doc(method))
        assert f">{method}<".encode() not in xml
