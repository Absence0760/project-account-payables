"""DIAN (Colombia) outbound generator + validator.

Pins the DIAN-profiled UBL 2.1 structure (Invoice root + cac/cbc/ext namespaces,
CustomizationID / ProfileID, supplier + customer party with the NIT,
LegalMonetaryTotal/PayableAmount, the UBLExtensions clearance placeholder),
money-as-2dp-string (Decimal-not-float), and the PII-free national validation
(NIT + payable_amount required, malformed NIT flagged) on the shared
EInvoiceDocument.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from lxml import etree

from app.services.e_invoice.country_formats.dian import (
    _NS_CAC,
    _NS_CBC,
    _NS_EXT,
    _NS_INVOICE,
    DIANFormat,
)
from app.services.e_invoice.country_formats.dispatcher import get_country_format
from app.services.e_invoice.model import (
    EInvoiceDocument,
    EInvoiceFormat,
    EInvoiceLine,
    EInvoiceParty,
    EInvoiceTax,
)


def _full_doc() -> EInvoiceDocument:
    return EInvoiceDocument(
        source_format=EInvoiceFormat.UBL,
        invoice_number="DIAN-2024-1",
        issue_date=date(2024, 5, 1),
        due_date=date(2024, 5, 31),
        currency="COP",
        invoice_type_code="01",
        seller=EInvoiceParty(
            name="Proveedor Colombia SAS",
            tax_id="900123456",  # 9-digit NIT
            address_lines=["Calle 100 # 7-21"],
            city="Bogotá",
            postal_code="110111",
            country_code="CO",
        ),
        buyer=EInvoiceParty(
            name="Comprador SAS",
            tax_id="800987654",
            country_code="CO",
        ),
        line_extension_amount=Decimal("1000000.00"),
        tax_exclusive_amount=Decimal("1000000.00"),
        tax_inclusive_amount=Decimal("1190000.00"),
        tax_total=Decimal("190000.00"),
        payable_amount=Decimal("1190000.00"),
        taxes=[
            EInvoiceTax(
                category="S",
                rate=Decimal("19.00"),
                taxable_amount=Decimal("1000000.00"),
                tax_amount=Decimal("190000.00"),
            )
        ],
        lines=[
            EInvoiceLine(
                line_id="1",
                item_code="SKU-A",
                description="Producto A",
                quantity=Decimal("10.0000"),
                unit_code="EA",
                unit_price=Decimal("60000.00"),
                line_total=Decimal("600000.00"),
            ),
            EInvoiceLine(
                line_id="2",
                item_code="SKU-B",
                description="Producto B",
                quantity=Decimal("4.0000"),
                unit_price=Decimal("100000.00"),
                line_total=Decimal("400000.00"),
            ),
        ],
    )


def test_registered_under_dian_code():
    fmt = get_country_format("dian")
    assert isinstance(fmt, DIANFormat)
    assert fmt.country == "CO"
    assert fmt.display_name == "DIAN (Colombia)"


def test_generate_returns_bytes_with_declaration():
    xml = DIANFormat().generate(_full_doc())
    assert isinstance(xml, bytes)
    assert xml.startswith(b"<?xml")


def test_root_namespace_and_nsmap():
    root = etree.fromstring(DIANFormat().generate(_full_doc()))
    assert etree.QName(root).namespace == _NS_INVOICE
    assert etree.QName(root).localname == "Invoice"
    nsmap = root.nsmap
    assert nsmap["cac"] == _NS_CAC
    assert nsmap["cbc"] == _NS_CBC
    assert nsmap["ext"] == _NS_EXT


def test_dian_profiling_header():
    root = etree.fromstring(DIANFormat().generate(_full_doc()))
    assert root.find(f"{{{_NS_CBC}}}CustomizationID").text == "10"
    assert root.find(f"{{{_NS_CBC}}}ProfileID").text == "DIAN 2.1: Factura Electrónica de Venta"
    assert root.find(f"{{{_NS_CBC}}}UBLVersionID").text == "UBL 2.1"
    assert root.find(f"{{{_NS_CBC}}}ID").text == "DIAN-2024-1"


def test_invoice_type_code_is_translated_from_uncl1001():
    """Regression: DIAN reads `InvoiceTypeCode` from its OWN document list.

    `EInvoiceDocument.invoice_type_code` is documented as UNCL1001 and the
    mapper always sets `380` (commercial invoice), so the raw pass-through
    emitted `380` — outside DIAN's `01`/`02`/`03`/`04` list — on every real
    export, and the module's own `01` default was unreachable.
    """
    doc = _full_doc()
    doc.invoice_type_code = "380"  # what mapper.invoice_to_einvoice_document sets
    root = etree.fromstring(DIANFormat().generate(doc))
    assert root.find(f"{{{_NS_CBC}}}InvoiceTypeCode").text == "01"


def test_invoice_type_code_passes_a_dian_code_through_and_defaults_otherwise():
    doc = _full_doc()
    doc.invoice_type_code = "02"  # factura de exportación
    root = etree.fromstring(DIANFormat().generate(doc))
    assert root.find(f"{{{_NS_CBC}}}InvoiceTypeCode").text == "02"

    doc.invoice_type_code = None
    root = etree.fromstring(DIANFormat().generate(doc))
    assert root.find(f"{{{_NS_CBC}}}InvoiceTypeCode").text == "01"

    doc.invoice_type_code = "999"  # unknown in either list
    root = etree.fromstring(DIANFormat().generate(doc))
    assert root.find(f"{{{_NS_CBC}}}InvoiceTypeCode").text == "01"


def test_ublextensions_clearance_placeholder_present():
    root = etree.fromstring(DIANFormat().generate(_full_doc()))
    ext = root.find(
        f"{{{_NS_EXT}}}UBLExtensions/{{{_NS_EXT}}}UBLExtension/{{{_NS_EXT}}}ExtensionContent"
    )
    # The envelope the CUFE / signature / DianExtensions are injected into at
    # clearance must be present (empty in this pre-clearance slice).
    assert ext is not None


def test_supplier_and_customer_party_nit():
    root = etree.fromstring(DIANFormat().generate(_full_doc()))
    supplier_id = root.find(
        f"{{{_NS_CAC}}}AccountingSupplierParty/{{{_NS_CAC}}}Party"
        f"/{{{_NS_CAC}}}PartyTaxScheme/{{{_NS_CBC}}}CompanyID"
    )
    assert supplier_id.text == "900123456"
    supplier_name = root.find(
        f"{{{_NS_CAC}}}AccountingSupplierParty/{{{_NS_CAC}}}Party"
        f"/{{{_NS_CAC}}}PartyName/{{{_NS_CBC}}}Name"
    )
    assert supplier_name.text == "Proveedor Colombia SAS"
    customer_id = root.find(
        f"{{{_NS_CAC}}}AccountingCustomerParty/{{{_NS_CAC}}}Party"
        f"/{{{_NS_CAC}}}PartyTaxScheme/{{{_NS_CBC}}}CompanyID"
    )
    assert customer_id.text == "800987654"


def test_legal_monetary_total_payable_2dp_with_currency():
    root = etree.fromstring(DIANFormat().generate(_full_doc()))
    payable = root.find(f"{{{_NS_CAC}}}LegalMonetaryTotal/{{{_NS_CBC}}}PayableAmount")
    assert payable is not None
    assert payable.get("currencyID") == "COP"
    # Money serializes as a 2dp string — proves Decimal, not float.
    assert payable.text == "1190000.00"


def test_invoice_lines():
    root = etree.fromstring(DIANFormat().generate(_full_doc()))
    lines = root.findall(f"{{{_NS_CAC}}}InvoiceLine")
    assert len(lines) == 2
    assert lines[0].find(f"{{{_NS_CBC}}}ID").text == "1"
    qty = lines[0].find(f"{{{_NS_CBC}}}InvoicedQuantity")
    assert qty.get("unitCode") == "EA"
    lea = lines[0].find(f"{{{_NS_CBC}}}LineExtensionAmount")
    assert lea.text == "600000.00"


def test_money_is_decimal_not_float():
    doc = _full_doc()
    doc.payable_amount = Decimal("100.005")  # ROUND_HALF_EVEN → 100.00
    root = etree.fromstring(DIANFormat().generate(doc))
    payable = root.find(f"{{{_NS_CAC}}}LegalMonetaryTotal/{{{_NS_CBC}}}PayableAmount")
    assert payable.text == "100.00"


def test_validate_clean_doc_has_no_errors():
    assert DIANFormat().validate(_full_doc()) == []


def test_validate_missing_seller_nit_is_pii_free():
    doc = _full_doc()
    doc.seller.tax_id = None
    errors = DIANFormat().validate(doc)
    codes = {e.field: e.code for e in errors}
    assert codes.get("seller.tax_id") == "missing"


def test_validate_missing_payable_amount():
    doc = _full_doc()
    doc.payable_amount = None
    errors = DIANFormat().validate(doc)
    codes = {e.field: e.code for e in errors}
    assert codes.get("payable_amount") == "missing"


def test_validate_malformed_nit_flagged_pii_free():
    doc = _full_doc()
    doc.seller.tax_id = "ABC"  # not 9–10 digits
    errors = DIANFormat().validate(doc)
    matching = [e for e in errors if e.field == "seller.tax_id"]
    assert matching and matching[0].code == "malformed"
    for err in errors:
        assert "ABC" not in str(err)
