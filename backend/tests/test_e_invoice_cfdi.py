"""CFDI 4.0 (Mexico) outbound generation + national validation.

Pins the cfdi:Comprobante root tag/namespace/version, the emisor/receptor RFC
attributes, the Conceptos lines, document totals, 2dp Decimal money, and the
PII-free FieldError contract (missing / malformed RFC, missing total/subtotal).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from lxml import etree

from app.services.e_invoice.country_formats.cfdi import _NS_CFDI, _VERSION, CFDIFormat
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
        invoice_number="MX-2024-9",
        issue_date=date(2024, 5, 1),
        due_date=date(2024, 5, 31),
        currency="MXN",
        invoice_type_code="380",
        seller=EInvoiceParty(
            name="Emisor SA de CV",
            tax_id="AAA010101AAA",
            address_lines=["Calle 1"],
            city="CDMX",
            postal_code="01000",
            country_code="MX",
        ),
        buyer=EInvoiceParty(
            name="Receptor SA de CV",
            tax_id="BBB020202BBB",
            address_lines=["Calle 2"],
            city="Monterrey",
            postal_code="64000",
            country_code="MX",
        ),
        line_extension_amount=Decimal("1000.00"),
        tax_exclusive_amount=Decimal("1000.00"),
        tax_inclusive_amount=Decimal("1160.00"),
        tax_total=Decimal("160.00"),
        payable_amount=Decimal("1160.00"),
        taxes=[
            EInvoiceTax(
                category="S",
                rate=Decimal("16.00"),
                taxable_amount=Decimal("1000.00"),
                tax_amount=Decimal("160.00"),
            )
        ],
        lines=[
            EInvoiceLine(
                line_id="1",
                description="Producto A",
                quantity=Decimal("10.0000"),
                unit_price=Decimal("100.00"),
                line_total=Decimal("1000.00"),
                tax_rate=Decimal("16.00"),
            )
        ],
    )


def _fmt() -> CFDIFormat:
    return CFDIFormat()


def test_registered_under_format_code():
    fmt = get_country_format("cfdi")
    assert fmt is not None
    assert fmt.format_code == "cfdi"
    assert fmt.country == "MX"


def test_generate_returns_bytes_with_declaration():
    xml = _fmt().generate(_full_doc())
    assert isinstance(xml, bytes)
    assert xml.startswith(b"<?xml")


def test_root_tag_namespace_and_version():
    root = etree.fromstring(_fmt().generate(_full_doc()))
    assert etree.QName(root).namespace == _NS_CFDI
    assert etree.QName(root).localname == "Comprobante"
    assert root.nsmap["cfdi"] == _NS_CFDI
    assert root.get("Version") == _VERSION


def test_comprobante_header_attributes():
    root = etree.fromstring(_fmt().generate(_full_doc()))
    assert root.get("Fecha") == "2024-05-01T00:00:00"
    assert root.get("Moneda") == "MXN"
    assert root.get("TipoDeComprobante") == "I"
    assert root.get("Exportacion") == "01"


def test_emisor_and_receptor_rfc():
    root = etree.fromstring(_fmt().generate(_full_doc()))
    ns = _NS_CFDI
    emisor = root.find(f"{{{ns}}}Emisor")
    receptor = root.find(f"{{{ns}}}Receptor")
    assert emisor.get("Rfc") == "AAA010101AAA"
    assert emisor.get("Nombre") == "Emisor SA de CV"
    assert emisor.get("RegimenFiscal") == "601"
    assert receptor.get("Rfc") == "BBB020202BBB"
    assert receptor.get("UsoCFDI") == "G03"
    assert receptor.get("DomicilioFiscalReceptor") == "64000"


def test_conceptos_line_present():
    root = etree.fromstring(_fmt().generate(_full_doc()))
    ns = _NS_CFDI
    conceptos = root.findall(f"{{{ns}}}Conceptos/{{{ns}}}Concepto")
    assert len(conceptos) == 1
    c = conceptos[0]
    assert c.get("Descripcion") == "Producto A"
    assert c.get("ClaveProdServ") == "01010101"
    assert c.get("ObjetoImp") == "02"


def test_seller_sku_goes_to_no_identificacion_not_the_sat_catalog_key():
    """Regression: `ClaveProdServ` is a SAT `c_ClaveProdServ` catalog key.

    `EInvoiceLine.item_code` is the SELLER's part number
    (`cac:SellersItemIdentification` / `ram:SellerAssignedID`), so emitting it
    as `ClaveProdServ` produced a CFDI the PAC refuses to stamp. CFDI has a
    dedicated attribute for the seller's identifier — `NoIdentificacion` — so
    the value is preserved, just in the right place.
    """
    doc = _full_doc()
    doc.lines[0].item_code = "SKU-A"
    root = etree.fromstring(_fmt().generate(doc))
    c = root.find(f"{{{_NS_CFDI}}}Conceptos/{{{_NS_CFDI}}}Concepto")
    assert c.get("ClaveProdServ") == "01010101"
    assert c.get("NoIdentificacion") == "SKU-A"


def test_no_identificacion_omitted_when_the_line_has_no_item_code():
    root = etree.fromstring(_fmt().generate(_full_doc()))
    c = root.find(f"{{{_NS_CFDI}}}Conceptos/{{{_NS_CFDI}}}Concepto")
    assert c.get("NoIdentificacion") is None


def test_money_serializes_as_2dp_decimal_not_float():
    root = etree.fromstring(_fmt().generate(_full_doc()))
    assert root.get("SubTotal") == "1000.00"
    assert root.get("Total") == "1160.00"
    ns = _NS_CFDI
    impuestos = root.find(f"{{{ns}}}Impuestos")
    assert impuestos.get("TotalImpuestosTrasladados") == "160.00"
    concepto = root.find(f"{{{ns}}}Conceptos/{{{ns}}}Concepto")
    assert concepto.get("Importe") == "1000.00"


def test_xml_escaping_of_party_name():
    doc = _full_doc()
    doc.seller.name = "Acme <Manufacturing> & Co."
    xml = _fmt().generate(doc)
    assert b"<Manufacturing>" not in xml.replace(b"&lt;Manufacturing&gt;", b"")
    root = etree.fromstring(xml)
    emisor = root.find(f"{{{_NS_CFDI}}}Emisor")
    assert emisor.get("Nombre") == "Acme <Manufacturing> & Co."


def test_validate_valid_doc_returns_empty():
    assert _fmt().validate(_full_doc()) == []


def test_validate_missing_receptor_rfc_is_pii_free():
    doc = _full_doc()
    doc.buyer.tax_id = None
    errors = _fmt().validate(doc)
    err = next(e for e in errors if e.field == "buyer.tax_id")
    assert err.code == "missing"
    assert "AAA010101AAA" not in str(err)


def test_validate_malformed_emisor_rfc():
    doc = _full_doc()
    doc.seller.tax_id = "NOTANRFC"
    errors = _fmt().validate(doc)
    err = next(e for e in errors if e.field == "seller.tax_id")
    assert err.code == "malformed"
    assert "NOTANRFC" not in str(err)


def test_validate_missing_payable_amount():
    doc = _full_doc()
    doc.payable_amount = None
    errors = _fmt().validate(doc)
    fields = {e.field for e in errors}
    assert "payable_amount" in fields
    for e in errors:
        assert "1160.00" not in str(e)


def test_validate_missing_subtotal():
    doc = _full_doc()
    doc.tax_exclusive_amount = None
    errors = _fmt().validate(doc)
    fields = {e.field for e in errors}
    assert "tax_exclusive_amount" in fields


# ---------------------------------------------------------------------------
# ObjetoImp and the per-line tax breakdown are ONE decision
# ---------------------------------------------------------------------------


def _conceptos(doc) -> list:
    root = etree.fromstring(_fmt().generate(doc))
    return root.findall(f"{{{_NS_CFDI}}}Conceptos/{{{_NS_CFDI}}}Concepto")


def _traslados(concepto) -> list:
    return concepto.findall(
        f"{{{_NS_CFDI}}}Impuestos/{{{_NS_CFDI}}}Traslados/{{{_NS_CFDI}}}Traslado"
    )


def test_objeto_imp_02_line_carries_its_traslado():
    """SAT requires the per-line `cfdi:Traslado` whenever a Concepto declares
    `ObjetoImp="02"` (subject to tax). The generator used to stamp "02" on every
    line and emit only the document-level total, so the file contradicted its
    own claim and a PAC would refuse to stamp it.

    `TasaOCuota` is a 6-dp FRACTION, not the model's percentage.
    """
    concepto = _conceptos(_full_doc())[0]
    assert concepto.get("ObjetoImp") == "02"

    traslados = _traslados(concepto)
    assert len(traslados) == 1
    t = traslados[0]
    assert t.get("Base") == "1000.00"
    assert t.get("Impuesto") == "002"  # IVA
    assert t.get("TipoFactor") == "Tasa"
    assert t.get("TasaOCuota") == "0.160000"  # 16.00% as a 6-dp fraction
    # Importe is defined as Base x TasaOCuota; the line carries no explicit
    # tax_amount here, so it is derived exactly.
    assert t.get("Importe") == "160.00"


def test_line_tax_amount_wins_over_the_derived_importe():
    doc = _full_doc()
    doc.lines[0].tax_amount = Decimal("159.99")
    t = _traslados(_conceptos(doc)[0])[0]
    assert t.get("Importe") == "159.99"


def test_line_without_its_own_rate_borrows_the_documents_single_rate():
    """`mapper.invoice_to_e_invoice` fills `EInvoiceLine.tax_amount` but never
    `tax_rate`, so without this fallback the ordinary single-rate invoice could
    never state a `TasaOCuota` and every line would drop to "03"."""
    doc = _full_doc()
    doc.lines[0].tax_rate = None
    doc.lines[0].tax_amount = Decimal("160.00")
    concepto = _conceptos(doc)[0]
    assert concepto.get("ObjetoImp") == "02"
    t = _traslados(concepto)[0]
    assert t.get("TasaOCuota") == "0.160000"
    assert t.get("Importe") == "160.00"


def test_taxed_line_with_no_establishable_rate_declares_03_and_no_breakdown():
    """Subject to tax, but the rate the breakdown needs cannot be established
    (two distinct document rates, so no line may borrow one).

    "03" says "subject to tax, not obliged to break it down" - exactly the
    situation. "01" would claim the line is not subject to tax at all, which is
    false, and "02" would require the breakdown we cannot build.
    """
    doc = _full_doc()
    doc.lines[0].tax_rate = None
    doc.lines[0].tax_amount = Decimal("160.00")
    doc.taxes = [
        EInvoiceTax(category="S", rate=Decimal("16.00"), tax_amount=Decimal("100.00")),
        EInvoiceTax(category="S", rate=Decimal("8.00"), tax_amount=Decimal("60.00")),
    ]
    concepto = _conceptos(doc)[0]
    assert concepto.get("ObjetoImp") == "03"
    assert concepto.find(f"{{{_NS_CFDI}}}Impuestos") is None


def test_untaxed_line_declares_01_and_no_breakdown():
    doc = _full_doc()
    doc.lines[0].tax_rate = None
    doc.lines[0].tax_amount = None
    doc.taxes = []
    doc.tax_total = None
    concepto = _conceptos(doc)[0]
    assert concepto.get("ObjetoImp") == "01"
    assert concepto.find(f"{{{_NS_CFDI}}}Impuestos") is None


def test_zero_rate_is_tasa_cero_not_exento():
    """A 0 rate in the normalized model means "taxed at 0%" (tasa cero), which
    SAT expresses as TipoFactor="Tasa" with TasaOCuota="0.000000" and
    Importe="0.00". "Exento" is a DIFFERENT claim (both attributes absent) and
    nothing in the model distinguishes the two, so it is never asserted."""
    doc = _full_doc()
    doc.lines[0].tax_rate = Decimal("0.00")
    doc.lines[0].tax_amount = Decimal("0.00")
    t = _traslados(_conceptos(doc)[0])[0]
    assert t.get("TipoFactor") == "Tasa"
    assert t.get("TasaOCuota") == "0.000000"
    assert t.get("Importe") == "0.00"


def test_document_impuestos_carries_the_traslados_breakdown():
    root = etree.fromstring(_fmt().generate(_full_doc()))
    impuestos = root.find(f"{{{_NS_CFDI}}}Impuestos")
    assert impuestos.get("TotalImpuestosTrasladados") == "160.00"
    doc_traslados = impuestos.findall(f"{{{_NS_CFDI}}}Traslados/{{{_NS_CFDI}}}Traslado")
    assert len(doc_traslados) == 1
    assert doc_traslados[0].get("Base") == "1000.00"
    assert doc_traslados[0].get("TasaOCuota") == "0.160000"
    assert doc_traslados[0].get("Importe") == "160.00"


def test_document_impuestos_omitted_entirely_when_there_is_no_tax():
    doc = _full_doc()
    doc.taxes = []
    doc.tax_total = None
    doc.lines[0].tax_rate = None
    doc.lines[0].tax_amount = None
    root = etree.fromstring(_fmt().generate(doc))
    assert root.find(f"{{{_NS_CFDI}}}Impuestos") is None
