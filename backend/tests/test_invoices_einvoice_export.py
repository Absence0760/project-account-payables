"""Authenticated UBL e-invoice export — GET /api/invoices/{id}/einvoice.

Covers the happy path for each allowed role, the 400 on a bad format, the 404
for an invoice not in the tenant DB, and the 422 (PII-free body) when the
mapped document is tax-invalid. Auth/role gating itself is covered by
test_rbac.py (the route carries require_roles and is not in NO_AUTH_REQUIRED).
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from app.models.entity import Entity
from app.models.invoice import Invoice, InvoiceLineItem, InvoiceStatus
from app.models.organization import Organization
from app.services.e_invoice import parse_cii, parse_ubl


async def _add_invoice(mk, org_id, *, number="INV-XP-1") -> str:
    async with mk() as s:
        inv = Invoice(
            organization_id=org_id,
            invoice_number=number,
            vendor_name="Vendor SARL",
            vendor_tax_id="FR40123456789",
            vendor_address="12 Rue de Paris\n75001 Paris",
            invoice_date=date(2024, 6, 1),
            due_date=date(2024, 7, 1),
            amount=Decimal("1200.00"),
            subtotal=Decimal("1000.00"),
            tax_amount=Decimal("200.00"),
            tax_rate=Decimal("20.00"),
            discount_amount=Decimal("0.00"),
            shipping_amount=Decimal("0.00"),
            currency="EUR",
            status=InvoiceStatus.approved,
        )
        s.add(inv)
        await s.flush()
        s.add(
            InvoiceLineItem(
                invoice_id=inv.id,
                line_number=1,
                item_code="SKU-A",
                description="Service",
                quantity=Decimal("2.0000"),
                unit_price=Decimal("500.00"),
                total=Decimal("1000.00"),
                tax=Decimal("200.00"),
            )
        )
        await s.commit()
        await s.refresh(inv)
        return str(inv.id)


async def _set_company(realdb, key, company: dict) -> None:
    ctrl_mk = realdb.control_sessionmaker()
    org_id = realdb.info(key).org_id
    async with ctrl_mk() as s:
        org = await s.get(Organization, org_id)
        settings = dict(org.settings or {})
        settings["company"] = company
        org.settings = settings
        await s.commit()


async def test_export_happy_path_each_role(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    inv_id = await _add_invoice(mk, org_id)

    for role in ("admin", "ap_manager", "cfo", "ap_clerk"):
        async with realdb.client(key="a", role=role) as c:
            resp = await c.get(f"/api/invoices/{inv_id}/einvoice")
        assert resp.status_code == 200, f"{role}: {resp.text}"
        assert resp.headers["content-type"].startswith("application/xml")
        assert "attachment" in resp.headers["content-disposition"]
        # The body is real, parseable UBL whose seller is the vendor.
        doc = parse_ubl(resp.content)
        assert doc.invoice_number == "INV-XP-1"
        assert doc.seller.name == "Vendor SARL"


async def test_export_filename_survives_quote_in_invoice_number(realdb):
    """`invoice_number` is AI-extracted / user-entered, not a strictly
    validated identifier — it can contain a `"`. A naive
    `f'attachment; filename="{name}"'` breaks the header's quoted-string
    syntax in that case (#188). The `filename="..."` fallback must not carry
    an unescaped embedded quote, and the RFC 5987 `filename*=` form must
    still carry the real, percent-encoded invoice number."""
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    inv_id = await _add_invoice(mk, org_id, number='INV-123"456')

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.get(f"/api/invoices/{inv_id}/einvoice")
    assert resp.status_code == 200, resp.text

    disposition = resp.headers["content-disposition"]
    # Exactly two double quotes — the open/close of filename="...". A third
    # (from the invoice number) would corrupt the quoted-string syntax.
    assert disposition.count('"') == 2
    filename_param = disposition.split('filename="', 1)[1].split('"', 1)[0]
    assert '"' not in filename_param
    # The percent-encoded RFC 5987 form still carries the real characters,
    # so a client that understands it saves the file under the true name.
    assert "filename*=UTF-8''" in disposition
    assert "einvoice-INV-123%22456.xml" in disposition


async def test_export_filename_strips_control_char_from_invoice_number(realdb):
    """A stray control character in `invoice_number` must not leak into the
    Content-Disposition filename fallback unescaped. Uses a tab (one of the
    few control characters XML 1.0 permits in element text — the UBL body
    itself embeds `invoice_number` verbatim) so this test isolates the
    header-safety bug from XML-generation validity, which is unaffected here."""
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    inv_id = await _add_invoice(mk, org_id, number="INV-CTRL\t-1")

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.get(f"/api/invoices/{inv_id}/einvoice")
    assert resp.status_code == 200, resp.text

    disposition = resp.headers["content-disposition"]
    filename_param = disposition.split('filename="', 1)[1].split('"', 1)[0]
    assert "\t" not in filename_param
    assert "filename*=UTF-8''" in disposition


async def test_export_unknown_invoice_404(realdb):
    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.get(f"/api/invoices/{uuid.uuid4()}/einvoice")
    assert resp.status_code == 404


async def test_export_tenant_isolation_404(realdb):
    """An invoice created in tenant A must 404 when fetched with tenant B."""
    mk_a = realdb.sessionmaker("a")
    org_a = realdb.info("a").org_id
    inv_id = await _add_invoice(mk_a, org_a, number="INV-ISO-XP")

    async with realdb.client(key="b", role="ap_manager") as c:
        resp = await c.get(f"/api/invoices/{inv_id}/einvoice")
    assert resp.status_code == 404


async def test_export_bad_format_400(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    inv_id = await _add_invoice(mk, org_id, number="INV-FMT")

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.get(f"/api/invoices/{inv_id}/einvoice?format=bogus")
    assert resp.status_code == 400


async def test_export_cii_happy_path(realdb):
    """`?format=cii` returns the built-in UN/CEFACT CII dialect (not UBL, not a
    national format). The body is real, parseable CII whose seller is the
    vendor, and the filename is format-tagged so it doesn't collide with UBL."""
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    inv_id = await _add_invoice(mk, org_id, number="INV-CII-1")

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.get(f"/api/invoices/{inv_id}/einvoice?format=cii")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/xml")
    assert "cii" in resp.headers["content-disposition"]
    doc = parse_cii(resp.content)
    assert doc.invoice_number == "INV-CII-1"
    assert doc.seller.name == "Vendor SARL"
    assert doc.seller.tax_id == "FR40123456789"


async def test_export_cii_tax_invalid_422_pii_free(realdb):
    """The CII path shares the same outbound tax guard as UBL: a buyer tax id
    malformed for its country 422s with a PII-free 'field: code' body."""
    await _set_company(
        realdb,
        "a",
        {"name": "Our Co", "tax_id": "DE12", "country_code": "DE"},  # malformed DE VAT
    )
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    inv_id = await _add_invoice(mk, org_id, number="INV-CII-422")

    try:
        async with realdb.client(key="a", role="ap_manager") as c:
            resp = await c.get(f"/api/invoices/{inv_id}/einvoice?format=cii")
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert "buyer.tax_id" in detail
        assert "malformed" in detail
        assert "DE12" not in detail  # PII-free
    finally:
        await _set_company(realdb, "a", {})


async def test_export_forbidden_role_403(realdb):
    """A role outside the allowed set is rejected. We use a vendor-typ user?
    No — RBAC is by role; the seeded users only hold one role each. The four
    allowed roles cover the matrix, so we assert the deny-path differently:
    an unauthenticated request is rejected."""
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    inv_id = await _add_invoice(mk, org_id, number="INV-NOAUTH")

    async with realdb.client(key="a", role=None) as c:
        resp = await c.get(f"/api/invoices/{inv_id}/einvoice")
    assert resp.status_code in (401, 403)


async def test_export_entity_name_as_buyer_name(realdb):
    """When the invoice carries an entity_id, the export resolves the Entity row
    and uses its name as the buyer (AccountingCustomerParty) name — exercising
    the otherwise-untested entity-lookup branch in the route."""
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id

    async with mk() as s:
        entity = Entity(
            organization_id=org_id,
            name="Acme Subsidiary GmbH",
            slug="acme-sub",
        )
        s.add(entity)
        await s.flush()
        entity_id = entity.id
        inv = Invoice(
            organization_id=org_id,
            entity_id=entity_id,
            invoice_number="INV-ENT-1",
            vendor_name="Vendor SARL",
            vendor_tax_id="FR40123456789",
            vendor_address="12 Rue de Paris\n75001 Paris",
            invoice_date=date(2024, 6, 1),
            due_date=date(2024, 7, 1),
            amount=Decimal("1200.00"),
            subtotal=Decimal("1000.00"),
            tax_amount=Decimal("200.00"),
            tax_rate=Decimal("20.00"),
            discount_amount=Decimal("0.00"),
            shipping_amount=Decimal("0.00"),
            currency="EUR",
            status=InvoiceStatus.approved,
        )
        s.add(inv)
        await s.flush()
        s.add(
            InvoiceLineItem(
                invoice_id=inv.id,
                line_number=1,
                description="Service",
                quantity=Decimal("2.0000"),
                unit_price=Decimal("500.00"),
                total=Decimal("1000.00"),
                tax=Decimal("200.00"),
            )
        )
        await s.commit()
        inv_id = str(inv.id)

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.get(f"/api/invoices/{inv_id}/einvoice")
    assert resp.status_code == 200, resp.text
    doc = parse_ubl(resp.content)
    assert doc.buyer.name == "Acme Subsidiary GmbH"


async def test_export_invalid_vendor_tax_id_422_for_known_country(realdb):
    """The outbound guard validates the SELLER (vendor) tax id too, not just the
    buyer. A malformed-for-its-country vendor VAT id (FR12, derived country FR
    from the FR prefix) must 422 with a PII-free seller.tax_id:malformed body —
    proving the supplier-side check is live, not inert."""
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    async with mk() as s:
        inv = Invoice(
            organization_id=org_id,
            invoice_number="INV-VTAX",
            vendor_name="Vendor SARL",
            vendor_tax_id="FR12",  # FR prefix → country FR, but malformed for FR
            vendor_address="12 Rue de Paris\n75001 Paris",
            invoice_date=date(2024, 6, 1),
            due_date=date(2024, 7, 1),
            amount=Decimal("1200.00"),
            subtotal=Decimal("1000.00"),
            tax_amount=Decimal("200.00"),
            tax_rate=Decimal("20.00"),
            discount_amount=Decimal("0.00"),
            shipping_amount=Decimal("0.00"),
            currency="EUR",
            status=InvoiceStatus.approved,
        )
        s.add(inv)
        await s.flush()
        s.add(
            InvoiceLineItem(
                invoice_id=inv.id,
                line_number=1,
                description="Service",
                quantity=Decimal("2.0000"),
                unit_price=Decimal("500.00"),
                total=Decimal("1000.00"),
                tax=Decimal("200.00"),
            )
        )
        await s.commit()
        inv_id = str(inv.id)

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.get(f"/api/invoices/{inv_id}/einvoice")
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert "seller.tax_id" in detail
    assert "malformed" in detail
    assert "FR12" not in detail  # PII-free


async def test_export_national_format_fatturapa(realdb):
    """`?format=fatturapa` dispatches through the country-format registry and
    returns the Italian FatturaPA dialect (not UBL). FatturaPA mandates both
    seller + buyer Partita IVA, so set an IT vendor + IT company identity."""
    from lxml import etree

    await _set_company(
        realdb,
        "a",
        {"name": "Nostra SRL", "tax_id": "IT12345678901", "country_code": "IT"},
    )
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    async with mk() as s:
        inv = Invoice(
            organization_id=org_id,
            invoice_number="INV-IT-1",
            vendor_name="Fornitore SPA",
            vendor_tax_id="IT98765432109",
            vendor_address="Via Roma 1\n00100 Roma",
            invoice_date=date(2024, 6, 1),
            due_date=date(2024, 7, 1),
            amount=Decimal("1220.00"),
            subtotal=Decimal("1000.00"),
            tax_amount=Decimal("220.00"),
            tax_rate=Decimal("22.00"),
            discount_amount=Decimal("0.00"),
            shipping_amount=Decimal("0.00"),
            currency="EUR",
            status=InvoiceStatus.approved,
        )
        s.add(inv)
        await s.flush()
        s.add(
            InvoiceLineItem(
                invoice_id=inv.id,
                line_number=1,
                description="Servizio",
                quantity=Decimal("2.0000"),
                unit_price=Decimal("500.00"),
                total=Decimal("1000.00"),
                tax=Decimal("220.00"),
            )
        )
        await s.commit()
        inv_id = str(inv.id)

    try:
        async with realdb.client(key="a", role="ap_manager") as c:
            resp = await c.get(f"/api/invoices/{inv_id}/einvoice?format=fatturapa")
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"].startswith("application/xml")
        # Filename is format-tagged so the UBL and national exports don't collide.
        assert "fatturapa" in resp.headers["content-disposition"]
        root = etree.fromstring(resp.content)
        assert root.tag.endswith("FatturaElettronica")
    finally:
        await _set_company(realdb, "a", {})


async def _add_national_invoice(
    mk, org_id, *, number, vendor_tax_id, currency="USD", vendor_name="Proveedor"
) -> str:
    """Add a minimal valid invoice with a chosen vendor tax id + currency, for
    the national-format route tests."""
    async with mk() as s:
        inv = Invoice(
            organization_id=org_id,
            invoice_number=number,
            vendor_name=vendor_name,
            vendor_tax_id=vendor_tax_id,
            vendor_address="Calle 1\nCiudad",
            invoice_date=date(2024, 6, 1),
            due_date=date(2024, 7, 1),
            amount=Decimal("1190.00"),
            subtotal=Decimal("1000.00"),
            tax_amount=Decimal("190.00"),
            tax_rate=Decimal("19.00"),
            discount_amount=Decimal("0.00"),
            shipping_amount=Decimal("0.00"),
            currency=currency,
            status=InvoiceStatus.approved,
        )
        s.add(inv)
        await s.flush()
        s.add(
            InvoiceLineItem(
                invoice_id=inv.id,
                line_number=1,
                description="Item",
                quantity=Decimal("2.0000"),
                unit_price=Decimal("500.00"),
                total=Decimal("1000.00"),
                tax=Decimal("190.00"),
            )
        )
        await s.commit()
        return str(inv.id)


async def test_export_national_format_cfdi(realdb):
    """`?format=cfdi` returns the Mexican CFDI 4.0 dialect. CFDI mandates both
    emisor + receptor RFC, so set an MX vendor + MX company identity."""
    from lxml import etree

    await _set_company(
        realdb, "a", {"name": "Nuestra SA", "tax_id": "XAXX010101000", "country_code": "MX"}
    )
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    inv_id = await _add_national_invoice(
        mk, org_id, number="INV-MX-1", vendor_tax_id="AAA010101AAA", currency="MXN"
    )
    try:
        async with realdb.client(key="a", role="ap_manager") as c:
            resp = await c.get(f"/api/invoices/{inv_id}/einvoice?format=cfdi")
        assert resp.status_code == 200, resp.text
        assert "cfdi" in resp.headers["content-disposition"]
        root = etree.fromstring(resp.content)
        assert root.tag.endswith("Comprobante")
        assert root.get("Version") == "4.0"
    finally:
        await _set_company(realdb, "a", {})


async def test_export_national_format_nfe(realdb):
    """`?format=nfe` returns the Brazilian NF-e dialect (emit CNPJ required;
    buyer name falls back to the org name)."""
    from lxml import etree

    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    inv_id = await _add_national_invoice(
        mk, org_id, number="INV-BR-1", vendor_tax_id="12345678000195", currency="BRL"
    )
    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.get(f"/api/invoices/{inv_id}/einvoice?format=nfe")
    assert resp.status_code == 200, resp.text
    assert "nfe" in resp.headers["content-disposition"]
    root = etree.fromstring(resp.content)
    assert root.tag.endswith("NFe")


async def test_export_national_format_dian(realdb):
    """`?format=dian` returns the Colombian DIAN-profiled UBL (supplier NIT
    required)."""
    from lxml import etree

    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    inv_id = await _add_national_invoice(
        mk, org_id, number="INV-CO-1", vendor_tax_id="900123456", currency="COP"
    )
    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.get(f"/api/invoices/{inv_id}/einvoice?format=dian")
    assert resp.status_code == 200, resp.text
    assert "dian" in resp.headers["content-disposition"]
    root = etree.fromstring(resp.content)
    assert root.tag.endswith("Invoice")
    # DIAN profiling marker present.
    assert b"DIAN 2.1" in resp.content


async def test_export_national_format_422_pii_free(realdb):
    """The national-format route propagates the format's own validation as a
    422 with a PII-free body. An NF-e with a malformed CNPJ → seller.tax_id:
    malformed, and the malformed value never appears in the body."""
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    inv_id = await _add_national_invoice(
        mk, org_id, number="INV-BR-BAD", vendor_tax_id="123", currency="BRL"
    )
    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.get(f"/api/invoices/{inv_id}/einvoice?format=nfe")
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert "seller.tax_id" in detail
    assert "malformed" in detail
    assert "123" not in detail  # PII-free


async def test_export_tax_invalid_422_pii_free(realdb):
    """When the mapped doc is tax-invalid (buyer tax id malformed for its
    country), the AP export 422s with a PII-free 'field: code' body."""
    await _set_company(
        realdb,
        "a",
        {"name": "Our Co", "tax_id": "DE12", "country_code": "DE"},  # malformed DE VAT
    )
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    inv_id = await _add_invoice(mk, org_id, number="INV-422")

    try:
        async with realdb.client(key="a", role="ap_manager") as c:
            resp = await c.get(f"/api/invoices/{inv_id}/einvoice")
        assert resp.status_code == 422, resp.text
        body = resp.json()
        detail = body["detail"]
        assert "buyer.tax_id" in detail
        assert "malformed" in detail
        # PII-free: the malformed value must not appear in the error body.
        assert "DE12" not in detail
    finally:
        await _set_company(realdb, "a", {})
