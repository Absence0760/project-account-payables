"""NF-e (Brazil) outbound generator + validator.

Pins the NF-e CORE subset structure (root namespace, infNFe@versao, ide, emit,
dest, det/prod, total/ICMSTot/vNF), money-as-2dp-string (Decimal-not-float), and
the PII-free national validation (CNPJ + payable_amount required, malformed CNPJ
flagged) on the shared EInvoiceDocument.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from lxml import etree

from app.services.e_invoice.country_formats.dispatcher import get_country_format
from app.services.e_invoice.country_formats.nfe import NFeFormat
from app.services.e_invoice.model import (
    EInvoiceDocument,
    EInvoiceFormat,
    EInvoiceLine,
    EInvoiceParty,
    EInvoiceTax,
)

_NS_NFE = "http://www.portalfiscal.inf.br/nfe"


def _q(parent, path: str):
    """Find by a slash-path of NF-e local names."""
    return parent.find("/".join(f"{{{_NS_NFE}}}{seg}" for seg in path.split("/")))


def _full_doc() -> EInvoiceDocument:
    return EInvoiceDocument(
        source_format=EInvoiceFormat.UBL,
        invoice_number="NFE-1001",
        issue_date=date(2024, 5, 1),
        due_date=date(2024, 5, 31),
        currency="BRL",
        invoice_type_code="380",
        order_reference="Venda",
        seller=EInvoiceParty(
            name="Fornecedor Brasil LTDA",
            tax_id="12345678000195",  # 14-digit CNPJ
            address_lines=["Avenida Paulista 1000"],
            city="São Paulo",
            postal_code="01310100",
            country_code="BR",
        ),
        buyer=EInvoiceParty(
            name="Comprador SA",
            tax_id="98765432000110",
            country_code="BR",
        ),
        line_extension_amount=Decimal("1000.00"),
        tax_exclusive_amount=Decimal("1000.00"),
        tax_inclusive_amount=Decimal("1180.00"),
        tax_total=Decimal("180.00"),
        payable_amount=Decimal("1180.00"),
        taxes=[
            EInvoiceTax(
                category="S",
                rate=Decimal("18.00"),
                taxable_amount=Decimal("1000.00"),
                tax_amount=Decimal("180.00"),
            )
        ],
        lines=[
            EInvoiceLine(
                line_id="1",
                item_code="SKU-A",
                description="Produto A",
                quantity=Decimal("10.0000"),
                unit_price=Decimal("60.00"),
                line_total=Decimal("600.00"),
            ),
            EInvoiceLine(
                line_id="2",
                item_code="SKU-B",
                description="Produto B",
                quantity=Decimal("4.0000"),
                unit_price=Decimal("100.00"),
                line_total=Decimal("400.00"),
            ),
        ],
    )


def test_registered_under_nfe_code():
    fmt = get_country_format("nfe")
    assert isinstance(fmt, NFeFormat)
    assert fmt.country == "BR"
    assert fmt.display_name == "NF-e (Brazil)"


def test_generate_returns_bytes_with_declaration():
    xml = NFeFormat().generate(_full_doc())
    assert isinstance(xml, bytes)
    assert xml.startswith(b"<?xml")


def test_root_namespace_and_infnfe_version():
    root = etree.fromstring(NFeFormat().generate(_full_doc()))
    assert etree.QName(root).namespace == _NS_NFE
    assert etree.QName(root).localname == "NFe"
    inf = _q(root, "infNFe")
    assert inf is not None
    assert inf.get("versao") == "4.00"
    assert inf.get("Id", "").startswith("NFe")
    # Id is the 44-digit chave-de-acesso shape: "NFe" + 44 chars.
    assert len(inf.get("Id")) == 3 + 44


def test_ide_block():
    root = etree.fromstring(NFeFormat().generate(_full_doc()))
    inf = _q(root, "infNFe")
    assert _q(inf, "ide/cUF").text == "35"
    assert _q(inf, "ide/natOp").text == "Venda"
    assert _q(inf, "ide/mod").text == "55"
    assert _q(inf, "ide/nNF").text == "NFE-1001"
    assert _q(inf, "ide/dhEmi").text == "2024-05-01T00:00:00"
    assert _q(inf, "ide/tpNF").text == "1"


def test_emit_seller_and_dest_buyer():
    root = etree.fromstring(NFeFormat().generate(_full_doc()))
    inf = _q(root, "infNFe")
    assert _q(inf, "emit/CNPJ").text == "12345678000195"
    assert _q(inf, "emit/xNome").text == "Fornecedor Brasil LTDA"
    assert _q(inf, "emit/enderEmit/xLgr").text == "Avenida Paulista 1000"
    assert _q(inf, "emit/enderEmit/xMun").text == "São Paulo"
    assert _q(inf, "dest/CNPJ").text == "98765432000110"
    assert _q(inf, "dest/xNome").text == "Comprador SA"


def test_det_lines():
    root = etree.fromstring(NFeFormat().generate(_full_doc()))
    inf = _q(root, "infNFe")
    dets = inf.findall(f"{{{_NS_NFE}}}det")
    assert len(dets) == 2
    assert dets[0].get("nItem") == "1"
    assert _q(dets[0], "prod/cProd").text == "SKU-A"
    assert _q(dets[0], "prod/xProd").text == "Produto A"
    assert _q(dets[0], "prod/vProd").text == "600.00"


def test_total_icmstot_vnf_2dp():
    root = etree.fromstring(NFeFormat().generate(_full_doc()))
    inf = _q(root, "infNFe")
    assert _q(inf, "total/ICMSTot/vProd").text == "1000.00"
    vnf = _q(inf, "total/ICMSTot/vNF")
    # Money serializes as a 2dp string — proves Decimal, not float.
    assert vnf.text == "1180.00"


def test_money_is_decimal_not_float():
    doc = _full_doc()
    doc.payable_amount = Decimal("100.005")  # ROUND_HALF_EVEN → 100.00
    root = etree.fromstring(NFeFormat().generate(doc))
    inf = _q(root, "infNFe")
    assert _q(inf, "total/ICMSTot/vNF").text == "100.00"


def test_validate_clean_doc_has_no_errors():
    assert NFeFormat().validate(_full_doc()) == []


def test_validate_missing_seller_cnpj_is_pii_free():
    doc = _full_doc()
    doc.seller.tax_id = None
    errors = NFeFormat().validate(doc)
    codes = {e.field: e.code for e in errors}
    assert codes.get("seller.tax_id") == "missing"


def test_validate_missing_payable_amount():
    doc = _full_doc()
    doc.payable_amount = None
    errors = NFeFormat().validate(doc)
    codes = {e.field: e.code for e in errors}
    assert codes.get("payable_amount") == "missing"


def test_validate_malformed_cnpj_flagged_pii_free():
    doc = _full_doc()
    doc.seller.tax_id = "123"  # not 14 digits
    errors = NFeFormat().validate(doc)
    matching = [e for e in errors if e.field == "seller.tax_id"]
    assert matching and matching[0].code == "malformed"
    # The malformed value must never leak into the error.
    for err in errors:
        assert "123" not in str(err)
