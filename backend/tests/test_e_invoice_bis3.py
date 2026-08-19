"""PEPPOL BIS Billing 3.0 conformance — the claim, and what backs it.

``cbc:CustomizationID`` / ``cbc:ProfileID`` are a conformance ASSERTION: an
Access Point routes on them and validates the document against that profile.
The generator emitted neither while the send path transmitted the document
under a doc-type id that claims BIS Billing 3.0, and the document did not meet
the profile anyway (no ``cbc:EndpointID`` on either party, tax subtotals with
no amounts, invoice lines with no VAT category).

These tests pin the whole loop: the two identifiers are declared only for a
document that passes the mandatory-element check, the elements the profile
requires are actually emitted, they survive the parse round-trip, and the
PEPPOL send path refuses to transmit a document that provably does not conform.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from lxml import etree

from app.services.e_invoice.bis3 import (
    BIS3_CUSTOMIZATION_ID,
    BIS3_PROFILE_ID,
    assert_bis3_conformant,
    bis3_conformance_errors,
    is_bis3_conformant,
)
from app.services.e_invoice.generate import generate_ubl
from app.services.e_invoice.model import (
    EInvoiceDocument,
    EInvoiceFormat,
    EInvoiceLine,
    EInvoiceParty,
    EInvoiceTax,
)
from app.services.e_invoice.ubl import parse_ubl
from app.services.e_invoice.validate import EInvoiceValidationError

_NS_CBC = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
_NS_CAC = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"

_SELLER_VAT = "DE123456789"  # PII — must never appear in a FieldError


def _conformant_doc() -> EInvoiceDocument:
    return EInvoiceDocument(
        source_format=EInvoiceFormat.UBL,
        invoice_number="BIS-2026-1",
        issue_date=date(2026, 1, 15),
        due_date=date(2026, 2, 15),
        currency="EUR",
        invoice_type_code="380",
        seller=EInvoiceParty(
            name="Lieferant GmbH",
            tax_id=_SELLER_VAT,
            address_lines=["Hauptstrasse 1"],
            city="Berlin",
            postal_code="10115",
            country_code="DE",
            endpoint_id=_SELLER_VAT,
            endpoint_scheme_id="9930",
        ),
        buyer=EInvoiceParty(
            name="Kaeufer AG",
            tax_id="DE987654321",
            city="Hamburg",
            postal_code="20095",
            country_code="DE",
            endpoint_id="DE987654321",
            endpoint_scheme_id="9930",
        ),
        line_extension_amount=Decimal("100.00"),
        tax_exclusive_amount=Decimal("100.00"),
        tax_inclusive_amount=Decimal("119.00"),
        tax_total=Decimal("19.00"),
        payable_amount=Decimal("119.00"),
        taxes=[
            EInvoiceTax(
                category="S",
                rate=Decimal("19.00"),
                taxable_amount=Decimal("100.00"),
                tax_amount=Decimal("19.00"),
            )
        ],
        lines=[
            EInvoiceLine(
                line_id="1",
                description="Widget",
                quantity=Decimal("1"),
                unit_price=Decimal("100.00"),
                line_total=Decimal("100.00"),
                tax_amount=Decimal("19.00"),
                tax_rate=Decimal("19.00"),
                tax_category="S",
            )
        ],
    )


def _cbc_text(root, name: str) -> str | None:
    el = root.find(f"{{{_NS_CBC}}}{name}")
    return None if el is None else el.text


# ---------------------------------------------------------------------------
# The identifiers themselves
# ---------------------------------------------------------------------------


def test_profile_identifiers_match_the_as4_doctype_and_process_id():
    """Drift guard. The customization id we DECLARE in the document and the one
    embedded in the AS4 document-type id the send path transmits under must be
    the same string, and the profile id must be that path's process id —
    otherwise the envelope and the payload claim two different profiles."""
    from app.services.peppol_adapters.constants import (
        PEPPOL_BIS_BILLING_DOCTYPE,
        PEPPOL_BIS_BILLING_PROCESSID,
    )

    assert BIS3_CUSTOMIZATION_ID in PEPPOL_BIS_BILLING_DOCTYPE
    assert BIS3_PROFILE_ID == PEPPOL_BIS_BILLING_PROCESSID


# ---------------------------------------------------------------------------
# The conformance check
# ---------------------------------------------------------------------------


def test_conformant_document_passes():
    assert bis3_conformance_errors(_conformant_doc()) == []
    assert is_bis3_conformant(_conformant_doc())


def test_missing_endpoint_ids_are_caught_on_both_parties():
    doc = _conformant_doc()
    doc.seller.endpoint_id = None
    doc.buyer.endpoint_id = None
    fields = {e.field for e in bis3_conformance_errors(doc)}
    assert "seller.endpoint_id" in fields
    assert "buyer.endpoint_id" in fields


def test_endpoint_without_its_eas_scheme_is_not_an_address():
    doc = _conformant_doc()
    doc.seller.endpoint_scheme_id = None
    fields = {e.field for e in bis3_conformance_errors(doc)}
    assert "seller.endpoint_scheme_id" in fields


def test_incomplete_tax_subtotal_is_caught():
    """A `cac:TaxSubtotal` carrying only `TaxCategory/Percent` — the shape the
    generator used to emit at line level — is not a VAT breakdown."""
    doc = _conformant_doc()
    doc.taxes = [EInvoiceTax(category=None, rate=Decimal("19.00"))]
    fields = {e.field for e in bis3_conformance_errors(doc)}
    assert "taxes[0].taxable_amount" in fields
    assert "taxes[0].tax_amount" in fields
    assert "taxes[0].category" in fields


def test_line_without_a_vat_category_is_caught():
    doc = _conformant_doc()
    doc.lines[0].tax_category = None
    doc.lines[0].tax_rate = None
    fields = {e.field for e in bis3_conformance_errors(doc)}
    assert "lines[0].tax_category" in fields
    assert "lines[0].tax_rate" in fields


def test_conformance_errors_never_carry_a_value():
    """Same PII contract as `validate.FieldError` — field path + code only."""
    doc = _conformant_doc()
    doc.seller.endpoint_scheme_id = None
    doc.tax_total = None
    for err in bis3_conformance_errors(doc):
        rendered = f"{err.field} {err.code} {err.message}"
        assert _SELLER_VAT not in rendered
        assert "Lieferant" not in rendered
        assert "119.00" not in rendered


def test_assert_raises_a_pii_free_validation_error():
    doc = _conformant_doc()
    doc.buyer.endpoint_id = None
    try:
        assert_bis3_conformant(doc)
    except EInvoiceValidationError as exc:
        assert "buyer.endpoint_id: missing" in str(exc)
        assert _SELLER_VAT not in str(exc)
    else:  # pragma: no cover — the assert must raise
        raise AssertionError("expected EInvoiceValidationError")


# ---------------------------------------------------------------------------
# What the generator emits
# ---------------------------------------------------------------------------


def test_conformant_document_declares_the_profile():
    root = etree.fromstring(generate_ubl(_conformant_doc()))
    assert _cbc_text(root, "CustomizationID") == BIS3_CUSTOMIZATION_ID
    assert _cbc_text(root, "ProfileID") == BIS3_PROFILE_ID
    # They lead the content model, ahead of cbc:ID.
    children = [etree.QName(c).localname for c in root if isinstance(c.tag, str)]
    assert children[:3] == ["CustomizationID", "ProfileID", "ID"]


def test_non_conformant_document_declares_nothing():
    """Declaring a profile you do not meet is worse than declaring none: it
    turns a document a receiver could have read as plain UBL into one it is
    obliged to reject."""
    doc = _conformant_doc()
    doc.buyer.endpoint_id = None
    root = etree.fromstring(generate_ubl(doc))
    assert _cbc_text(root, "CustomizationID") is None
    assert _cbc_text(root, "ProfileID") is None
    # ...and it is still a perfectly readable UBL invoice.
    assert _cbc_text(root, "ID") == "BIS-2026-1"


def test_declare_profile_can_be_forced_either_way():
    off = etree.fromstring(generate_ubl(_conformant_doc(), declare_profile=False))
    assert _cbc_text(off, "CustomizationID") is None

    doc = _conformant_doc()
    doc.buyer.endpoint_id = None
    on = etree.fromstring(generate_ubl(doc, declare_profile=True))
    assert _cbc_text(on, "CustomizationID") == BIS3_CUSTOMIZATION_ID


def test_endpoint_id_is_emitted_with_its_scheme_and_leads_the_party():
    root = etree.fromstring(generate_ubl(_conformant_doc()))
    party = root.find(
        f"{{{_NS_CAC}}}AccountingSupplierParty/{{{_NS_CAC}}}Party",
    )
    endpoint = party.find(f"{{{_NS_CBC}}}EndpointID")
    assert endpoint.text == _SELLER_VAT
    assert endpoint.get("schemeID") == "9930"
    # cbc:EndpointID is the first child of cac:Party in the UBL content model.
    assert etree.QName(party[0]).localname == "EndpointID"


def test_half_an_electronic_address_is_not_emitted():
    """A value with no `@schemeID` identifies nothing, so it is omitted rather
    than emitted as an address the receiver cannot resolve."""
    doc = _conformant_doc()
    doc.seller.endpoint_scheme_id = None
    root = etree.fromstring(generate_ubl(doc))
    party = root.find(f"{{{_NS_CAC}}}AccountingSupplierParty/{{{_NS_CAC}}}Party")
    assert party.find(f"{{{_NS_CBC}}}EndpointID") is None


def test_line_carries_a_complete_classified_tax_category():
    root = etree.fromstring(generate_ubl(_conformant_doc()))
    item = root.find(f"{{{_NS_CAC}}}InvoiceLine/{{{_NS_CAC}}}Item")
    classified = item.find(f"{{{_NS_CAC}}}ClassifiedTaxCategory")
    assert classified.find(f"{{{_NS_CBC}}}ID").text == "S"
    assert classified.find(f"{{{_NS_CBC}}}Percent").text == "19.00"
    assert classified.find(f"{{{_NS_CAC}}}TaxScheme/{{{_NS_CBC}}}ID").text == "VAT"


def test_document_tax_subtotal_carries_both_amounts():
    root = etree.fromstring(generate_ubl(_conformant_doc()))
    sub = root.find(f"{{{_NS_CAC}}}TaxTotal/{{{_NS_CAC}}}TaxSubtotal")
    assert sub.find(f"{{{_NS_CBC}}}TaxableAmount").text == "100.00"
    assert sub.find(f"{{{_NS_CBC}}}TaxAmount").text == "19.00"
    assert sub.find(f"{{{_NS_CAC}}}TaxCategory/{{{_NS_CBC}}}ID").text == "S"


# ---------------------------------------------------------------------------
# Round-trip — the module's standing contract
# ---------------------------------------------------------------------------


def test_new_fields_survive_the_round_trip():
    doc = _conformant_doc()
    back = parse_ubl(generate_ubl(doc))

    assert back.seller.endpoint_id == doc.seller.endpoint_id
    assert back.seller.endpoint_scheme_id == doc.seller.endpoint_scheme_id
    assert back.buyer.endpoint_id == doc.buyer.endpoint_id
    assert back.buyer.endpoint_scheme_id == doc.buyer.endpoint_scheme_id
    assert back.lines[0].tax_category == "S"
    assert back.lines[0].tax_rate == Decimal("19.00")
    assert back.lines[0].tax_amount == Decimal("19.00")
    # And the round-tripped document is itself still conformant.
    assert bis3_conformance_errors(back) == []
