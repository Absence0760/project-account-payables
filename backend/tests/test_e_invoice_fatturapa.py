"""FatturaPA (Italy) outbound generation + national validation.

Pins the FatturaPA root tag/namespace/version, the cedente/cessionario party
shape, the body totals, at least one line, 2dp Decimal money serialization, and
the PII-free FieldError contract (missing / malformed tax id, missing total).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from lxml import etree

from app.services.e_invoice.country_formats.dispatcher import get_country_format
from app.services.e_invoice.country_formats.fatturapa import (
    _NS_FATTURAPA,
    _VERSIONE,
    FatturaPAFormat,
)
from app.services.e_invoice.model import (
    EInvoiceDocument,
    EInvoiceFormat,
    EInvoiceLine,
    EInvoiceParty,
    EInvoiceTax,
)


def _q(parent, path):
    """Namespaced findall helper for the single FatturaPA namespace."""
    return parent.findall(path.format(ns=_NS_FATTURAPA))


def _full_doc() -> EInvoiceDocument:
    return EInvoiceDocument(
        source_format=EInvoiceFormat.UBL,
        invoice_number="IT-2024-77",
        issue_date=date(2024, 5, 1),
        due_date=date(2024, 5, 31),
        currency="EUR",
        invoice_type_code="380",
        buyer_reference="ABCDEF1",
        seller=EInvoiceParty(
            name="Fornitore S.r.l.",
            tax_id="IT12345678901",
            address_lines=["Via Roma 1"],
            city="Milano",
            postal_code="20100",
            country_code="IT",
        ),
        buyer=EInvoiceParty(
            name="Cliente S.p.A.",
            tax_id="IT98765432109",
            address_lines=["Via Torino 2"],
            city="Roma",
            postal_code="00100",
            country_code="IT",
        ),
        line_extension_amount=Decimal("1000.00"),
        tax_exclusive_amount=Decimal("1000.00"),
        tax_inclusive_amount=Decimal("1220.00"),
        tax_total=Decimal("220.00"),
        payable_amount=Decimal("1220.00"),
        taxes=[
            EInvoiceTax(
                category="S",
                rate=Decimal("22.00"),
                taxable_amount=Decimal("1000.00"),
                tax_amount=Decimal("220.00"),
            )
        ],
        lines=[
            EInvoiceLine(
                line_id="1",
                description="Widget A",
                quantity=Decimal("10.0000"),
                unit_price=Decimal("100.00"),
                line_total=Decimal("1000.00"),
                tax_rate=Decimal("22.00"),
            )
        ],
    )


def _fmt() -> FatturaPAFormat:
    return FatturaPAFormat()


def test_registered_under_format_code():
    fmt = get_country_format("fatturapa")
    assert fmt is not None
    assert fmt.format_code == "fatturapa"
    assert fmt.country == "IT"


def test_generate_returns_bytes_with_declaration():
    xml = _fmt().generate(_full_doc())
    assert isinstance(xml, bytes)
    assert xml.startswith(b"<?xml")


def test_root_tag_namespace_and_version():
    root = etree.fromstring(_fmt().generate(_full_doc()))
    assert etree.QName(root).namespace == _NS_FATTURAPA
    assert etree.QName(root).localname == "FatturaElettronica"
    assert root.get("versione") == _VERSIONE


def test_cedente_and_cessionario_present():
    root = etree.fromstring(_fmt().generate(_full_doc()))
    ns = _NS_FATTURAPA
    header = root.find(f"{{{ns}}}FatturaElettronicaHeader")
    cedente = header.find(f"{{{ns}}}CedentePrestatore")
    cessionario = header.find(f"{{{ns}}}CessionarioCommittente")
    assert cedente is not None
    assert cessionario is not None

    sel_id = cedente.find(f"{{{ns}}}DatiAnagrafici/{{{ns}}}IdFiscaleIVA/{{{ns}}}IdCodice")
    sel_name = cedente.find(f"{{{ns}}}DatiAnagrafici/{{{ns}}}Anagrafica/{{{ns}}}Denominazione")
    assert sel_id.text == "IT12345678901"
    assert sel_name.text == "Fornitore S.r.l."

    buy_id = cessionario.find(f"{{{ns}}}DatiAnagrafici/{{{ns}}}IdFiscaleIVA/{{{ns}}}IdCodice")
    assert buy_id.text == "IT98765432109"


def test_codice_destinatario_from_buyer_reference():
    root = etree.fromstring(_fmt().generate(_full_doc()))
    ns = _NS_FATTURAPA
    code = root.find(
        f"{{{ns}}}FatturaElettronicaHeader/{{{ns}}}DatiTrasmissione/{{{ns}}}CodiceDestinatario"
    )
    assert code.text == "ABCDEF1"


def test_codice_destinatario_default_when_absent():
    doc = _full_doc()
    doc.buyer_reference = None
    root = etree.fromstring(_fmt().generate(doc))
    ns = _NS_FATTURAPA
    code = root.find(
        f"{{{ns}}}FatturaElettronicaHeader/{{{ns}}}DatiTrasmissione/{{{ns}}}CodiceDestinatario"
    )
    assert code.text == "0000000"


def test_body_document_fields_and_line():
    root = etree.fromstring(_fmt().generate(_full_doc()))
    ns = _NS_FATTURAPA
    body = root.find(f"{{{ns}}}FatturaElettronicaBody")
    documento = body.find(f"{{{ns}}}DatiGenerali/{{{ns}}}DatiGeneraliDocumento")
    assert documento.find(f"{{{ns}}}TipoDocumento").text == "TD01"
    assert documento.find(f"{{{ns}}}Divisa").text == "EUR"
    assert documento.find(f"{{{ns}}}Data").text == "2024-05-01"
    assert documento.find(f"{{{ns}}}Numero").text == "IT-2024-77"

    lines = body.findall(f"{{{ns}}}DatiBeniServizi/{{{ns}}}DettaglioLinee")
    assert len(lines) == 1
    assert lines[0].find(f"{{{ns}}}Descrizione").text == "Widget A"
    assert lines[0].find(f"{{{ns}}}AliquotaIVA").text == "22.00"


def test_money_serializes_as_2dp_decimal_not_float():
    root = etree.fromstring(_fmt().generate(_full_doc()))
    ns = _NS_FATTURAPA
    documento = root.find(
        f"{{{ns}}}FatturaElettronicaBody/{{{ns}}}DatiGenerali/{{{ns}}}DatiGeneraliDocumento"
    )
    total = documento.find(f"{{{ns}}}ImportoTotaleDocumento")
    assert total.text == "1220.00"

    riepilogo = root.find(
        f"{{{ns}}}FatturaElettronicaBody/{{{ns}}}DatiBeniServizi/{{{ns}}}DatiRiepilogo"
    )
    assert riepilogo.find(f"{{{ns}}}ImponibileImporto").text == "1000.00"
    assert riepilogo.find(f"{{{ns}}}Imposta").text == "220.00"


def test_xml_escaping_of_party_name():
    doc = _full_doc()
    doc.seller.name = "Acme <Manufacturing> & Co."
    xml = _fmt().generate(doc)
    # Literal markup must never appear unescaped.
    assert b"<Manufacturing>" not in xml.replace(b"&lt;Manufacturing&gt;", b"")
    root = etree.fromstring(xml)
    ns = _NS_FATTURAPA
    name = root.find(
        f"{{{ns}}}FatturaElettronicaHeader/{{{ns}}}CedentePrestatore"
        f"/{{{ns}}}DatiAnagrafici/{{{ns}}}Anagrafica/{{{ns}}}Denominazione"
    )
    assert name.text == "Acme <Manufacturing> & Co."


def test_validate_valid_doc_returns_empty():
    assert _fmt().validate(_full_doc()) == []


def test_validate_missing_seller_tax_id_is_pii_free():
    doc = _full_doc()
    doc.seller.tax_id = None
    errors = _fmt().validate(doc)
    err = next(e for e in errors if e.field == "seller.tax_id")
    assert err.code == "missing"
    # The original (now removed) value can't leak; assert no buyer value leaks.
    assert "IT98765432109" not in str(err)


def test_validate_malformed_receptor_tax_id():
    doc = _full_doc()
    doc.buyer.tax_id = "NOTANRFC"
    errors = _fmt().validate(doc)
    err = next(e for e in errors if e.field == "buyer.tax_id")
    assert err.code == "malformed"
    assert "NOTANRFC" not in str(err)


def test_validate_missing_payable_amount():
    doc = _full_doc()
    doc.payable_amount = None
    errors = _fmt().validate(doc)
    fields = {e.field for e in errors}
    assert "payable_amount" in fields
    for e in errors:
        assert "1220.00" not in str(e)
