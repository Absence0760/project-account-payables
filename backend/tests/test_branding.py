"""White-label / partner branding (per-tenant brand config).

Coverage:

  * BrandConfig schema validation — hex-color + http(s)-URL guards, name strip.
  * GET /api/organization/branding — readable by any authed role, empty default.
  * PUT /api/organization/branding — admin-only mutate, persists to
    settings.brand, audits `organization.branding_updated` (PII-free), 422 on
    bad hex / bad URL, 401 without auth.
  * get_brand_context — resolution + platform-default fallback + malformed
    tolerance (branding.py).
  * Branded outbound surfaces — remittance / 1099 / audit PDFs carry the tenant
    product name + accent; logo-fetch failure falls back to the product-name
    text and never breaks PDF generation.
  * Branded emails — the email adapters apply the tenant product-name From
    display name + HTML header + support footer.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import fitz  # PyMuPDF, already pinned in backend/pyproject.toml
import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.models.workflow import AuditLog
from app.schemas.organization import BrandConfig
from app.services import branding
from app.services.branding import (
    PLATFORM_ACCENT_COLOR,
    PLATFORM_PRODUCT_NAME,
    BrandContext,
    brand_email_footer_html,
    brand_email_footer_text,
    brand_email_from,
    brand_email_html_header,
    get_brand_context,
)


def _pdf_text(pdf_bytes: bytes) -> str:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        return "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# Pure schema validation
# ---------------------------------------------------------------------------


def test_brand_defaults_are_empty():
    b = BrandConfig()
    assert b.product_name == ""
    assert b.logo_url == ""
    assert b.accent_color == ""
    assert b.accent_strong_color == ""


def test_brand_accepts_valid_values():
    b = BrandConfig(
        product_name="  Acme Pay  ",
        logo_url="https://cdn.acme.test/logo.png",
        accent_color="#638cff",
        accent_strong_color="#abc",
        support_url="https://help.acme.test",
        legal_url="https://acme.test/legal",
    )
    assert b.product_name == "Acme Pay"  # stripped
    assert b.accent_color == "#638cff"
    assert b.accent_strong_color == "#abc"


@pytest.mark.parametrize("bad", ["638cff", "#zzzzzz", "#12", "rgb(1,2,3)", "red", "#1234"])
def test_brand_rejects_bad_hex(bad):
    with pytest.raises(ValidationError):
        BrandConfig(accent_color=bad)


@pytest.mark.parametrize(
    "bad",
    [
        "javascript:alert(1)",
        "data:text/html,<script>",
        "ftp://x/y",
        "/relative/path",
        "logo.png",
    ],
)
def test_brand_rejects_bad_url(bad):
    with pytest.raises(ValidationError):
        BrandConfig(logo_url=bad)


# ---------------------------------------------------------------------------
# Endpoint — real Postgres + ASGI app
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_branding_any_authed_role(realdb):
    """Read is open to any authenticated org role (the app themes itself)."""
    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await c.get("/api/organization/branding")
    assert resp.status_code == 200
    body = resp.json()
    # Shape is the full BrandConfig regardless of whether anything is set.
    assert set(body) == {
        "product_name",
        "logo_url",
        "accent_color",
        "accent_strong_color",
        "support_url",
        "legal_url",
        # The tenant's vanity base URL — empty means "use the platform's
        # `FEOH_TENANT_URL_TEMPLATE`". See `test_branding_tenant_url_template.py`.
        "tenant_url_template",
        # Where this tenant's SSO callbacks land — a SEPARATE opt-in from the
        # field above, because it is registered at the customer's IdP and must
        # not move when an admin re-points outbound links. Empty means the
        # platform template. See `test_sso_custom_domain.py`.
        "sso_callback_base_url",
    }


@pytest.mark.asyncio
async def test_get_branding_requires_auth(realdb):
    async with realdb.client(key="a", role=None) as c:
        resp = await c.get("/api/organization/branding")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_put_branding_updates_persists_and_audits(realdb):
    payload = {
        "product_name": "Acme Pay",
        "logo_url": "https://cdn.acme.test/logo.png",
        "accent_color": "#112233",
        "accent_strong_color": "#0a1622",
        "support_url": "https://help.acme.test",
        "legal_url": "",
    }
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.put("/api/organization/branding", json=payload)
    assert resp.status_code == 200
    assert resp.json()["product_name"] == "Acme Pay"
    assert resp.json()["accent_color"] == "#112233"

    # Persisted on org settings.brand.
    cmk = realdb.control_sessionmaker()
    from app.models.organization import Organization

    async with cmk() as s:
        org = (
            await s.execute(select(Organization).where(Organization.id == realdb.info("a").org_id))
        ).scalar_one()
    assert org.settings["brand"]["product_name"] == "Acme Pay"
    assert org.settings["brand"]["accent_color"] == "#112233"

    # Audit row landed in the tenant trail, and it is PII-free (booleans only).
    tmk = realdb.sessionmaker("a")
    async with tmk() as s:
        rows = (
            (
                await s.execute(
                    select(AuditLog).where(AuditLog.action == "organization.branding_updated")
                )
            )
            .scalars()
            .all()
        )
    assert rows
    details = rows[-1].details
    assert details["product_name_set"] is True
    assert details["legal_url_set"] is False
    # No raw branding values leaked into the audit trail.
    assert "Acme Pay" not in str(details)
    assert "cdn.acme.test" not in str(details)


@pytest.mark.asyncio
async def test_put_branding_round_trips_to_get(realdb):
    async with realdb.client(key="a", role="admin") as c:
        await c.put(
            "/api/organization/branding",
            json={"product_name": "Roundtrip Co", "accent_color": "#abcdef"},
        )
    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.get("/api/organization/branding")
    assert resp.status_code == 200
    assert resp.json()["product_name"] == "Roundtrip Co"
    assert resp.json()["accent_color"] == "#abcdef"


@pytest.mark.asyncio
async def test_put_branding_rejects_bad_hex(realdb):
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.put("/api/organization/branding", json={"accent_color": "blue"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_put_branding_rejects_bad_url(realdb):
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.put("/api/organization/branding", json={"logo_url": "javascript:alert(1)"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_put_branding_admin_only(realdb):
    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.put("/api/organization/branding", json={"product_name": "Nope"})
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# get_brand_context — resolution + platform-default fallback
# ---------------------------------------------------------------------------


def test_brand_context_platform_defaults_on_none():
    b = get_brand_context(None)
    assert b.product_name == PLATFORM_PRODUCT_NAME
    assert b.accent_color == PLATFORM_ACCENT_COLOR
    assert b.logo_url == ""
    assert b.support_url == ""
    assert b.legal_url == ""
    assert b.has_logo is False
    assert b.has_support_url is False


def test_brand_context_platform_defaults_on_empty_brand_block():
    assert get_brand_context({}).product_name == PLATFORM_PRODUCT_NAME
    assert get_brand_context({"brand": {}}).accent_color == PLATFORM_ACCENT_COLOR
    # Non-dict brand block tolerated.
    assert get_brand_context({"brand": "nope"}).product_name == PLATFORM_PRODUCT_NAME


def test_brand_context_resolves_configured_values():
    b = get_brand_context(
        {
            "brand": {
                "product_name": "Acme Pay",
                "logo_url": "https://cdn.acme.test/logo.png",
                "accent_color": "#112233",
                "support_url": "https://help.acme.test",
                "legal_url": "https://acme.test/legal",
            }
        }
    )
    assert b.product_name == "Acme Pay"
    assert b.logo_url == "https://cdn.acme.test/logo.png"
    assert b.accent_color == "#112233"
    assert b.support_url == "https://help.acme.test"
    assert b.has_logo is True
    assert b.has_support_url is True


def test_brand_context_falls_back_on_malformed_fields():
    # A persisted-but-invalid block must degrade per-field, never raise.
    b = get_brand_context(
        {
            "brand": {
                "product_name": "   ",  # blank → platform default
                "accent_color": "not-a-hex",  # invalid → platform accent
                "logo_url": "javascript:alert(1)",  # bad scheme → empty
                "support_url": "/relative",  # not http(s) → empty
            }
        }
    )
    assert b.product_name == PLATFORM_PRODUCT_NAME
    assert b.accent_color == PLATFORM_ACCENT_COLOR
    assert b.logo_url == ""
    assert b.support_url == ""


# ---------------------------------------------------------------------------
# Logo fetch — bounded + fail-soft
# ---------------------------------------------------------------------------


def test_fetch_logo_bytes_none_url_returns_none():
    assert branding.fetch_logo_bytes(None) is None
    assert branding.fetch_logo_bytes("") is None
    # Bad scheme rejected before any network attempt.
    assert branding.fetch_logo_bytes("javascript:alert(1)") is None


def test_build_logo_flowable_returns_none_when_fetch_fails(monkeypatch):
    # Simulate a fetch failure (unreachable CDN / timeout) — the PDF path must
    # fall back to the product-name text, so the flowable is None.
    monkeypatch.setattr(branding, "fetch_logo_bytes", lambda url: None)
    brand = get_brand_context({"brand": {"logo_url": "https://cdn.acme.test/logo.png"}})
    flowable = branding.build_logo_flowable(brand, max_width_pt=100, max_height_pt=40)
    assert flowable is None


def test_build_logo_flowable_returns_none_on_undecodable_bytes(monkeypatch):
    # A 200 that isn't a valid image must not blow up — it falls back to text.
    monkeypatch.setattr(branding, "fetch_logo_bytes", lambda url: b"not-an-image")
    brand = get_brand_context({"brand": {"logo_url": "https://cdn.acme.test/x.png"}})
    assert branding.build_logo_flowable(brand, max_width_pt=100, max_height_pt=40) is None


# ---------------------------------------------------------------------------
# Branded PDFs — remittance / 1099 / audit
# ---------------------------------------------------------------------------


def _remittance_ctx(brand: BrandContext):
    from app.services.remittance_pdf import RemittanceContext, RemittanceLine

    return RemittanceContext(
        payer_name="Acme Inc.",
        payer_address="100 Main Street",
        vendor_name="Office Supplies Co.",
        vendor_address="50 Vendor Way",
        payment_date=datetime(2026, 5, 10, 10, 0),
        payment_method="ach",
        payment_reference="MOCK-ACH-1234",
        payment_amount=Decimal("1234.56"),
        currency="USD",
        lines=[
            RemittanceLine(
                invoice_number="INV-1", description="Supplies", amount=Decimal("1234.56")
            )
        ],
        brand=brand,
    )


def test_remittance_pdf_renders_brand_product_name(monkeypatch):
    # No logo configured → product-name text header. Force fetch to None so the
    # test is hermetic (no network).
    monkeypatch.setattr(branding, "fetch_logo_bytes", lambda url: None)
    from app.services.remittance_pdf import render_remittance_pdf

    brand = get_brand_context(
        {"brand": {"product_name": "Acme Pay", "support_url": "https://help.acme.test"}}
    )
    pdf = render_remittance_pdf(_remittance_ctx(brand))
    assert pdf.startswith(b"%PDF-")
    text = _pdf_text(pdf)
    assert "Acme Pay" in text  # branded header
    assert "help.acme.test" in text  # branded support footer
    # The money is still exact + present.
    assert "1,234.56" in text


def test_remittance_pdf_defaults_to_platform_brand(monkeypatch):
    monkeypatch.setattr(branding, "fetch_logo_bytes", lambda url: None)
    from app.services.remittance_pdf import render_remittance_pdf

    pdf = render_remittance_pdf(_remittance_ctx(get_brand_context(None)))
    text = _pdf_text(pdf)
    assert PLATFORM_PRODUCT_NAME in text


def test_remittance_pdf_logo_failure_falls_back_to_text(monkeypatch):
    # A configured logo whose fetch fails must still render the PDF (text header).
    monkeypatch.setattr(branding, "fetch_logo_bytes", lambda url: None)
    from app.services.remittance_pdf import render_remittance_pdf

    brand = get_brand_context(
        {"brand": {"product_name": "Acme Pay", "logo_url": "https://cdn.acme.test/down.png"}}
    )
    pdf = render_remittance_pdf(_remittance_ctx(brand))
    assert pdf.startswith(b"%PDF-")
    assert "Acme Pay" in _pdf_text(pdf)


def test_1099_pdf_renders_brand(monkeypatch):
    monkeypatch.setattr(branding, "fetch_logo_bytes", lambda url: None)
    from app.services.tax_1099_forms import FORM_NEC, Form1099Context, render_1099_pdf

    ctx = Form1099Context(
        tax_year=2025,
        form_type=FORM_NEC,
        payer_name="Acme Inc.",
        payer_tin_masked="**-***6789",
        payer_address="100 Main St",
        recipient_name="Vendor LLC",
        recipient_tin_masked="**-***4321",
        recipient_address="50 Vendor Way",
        box_label="Box 1 — Nonemployee compensation",
        box_amount=Decimal("5000.00"),
        generated_at=date(2026, 1, 31),
        brand=get_brand_context({"brand": {"product_name": "Acme Pay"}}),
    )
    pdf = render_1099_pdf(ctx)
    assert pdf.startswith(b"%PDF-")
    text = _pdf_text(pdf)
    assert "Acme Pay" in text
    assert "5,000.00" in text


def test_audit_report_pdf_renders_brand(monkeypatch):
    monkeypatch.setattr(branding, "fetch_logo_bytes", lambda url: None)
    from app.services.audit_report_pdf import AuditReportContext, render_audit_report_pdf

    ctx = AuditReportContext(
        org_name="Acme Inc.",
        scope="range",
        scope_label="Jan 1 2026 – Mar 31 2026",
        generated_at=datetime(2026, 4, 1, 9, 0),
        generated_by_name="Audit Bot",
        generated_by_email="audit@acme.test",
        entries=[],
        brand=get_brand_context({"brand": {"product_name": "Acme Pay"}}),
    )
    pdf = render_audit_report_pdf(ctx)
    assert pdf.startswith(b"%PDF-")
    assert "Acme Pay" in _pdf_text(pdf)


# ---------------------------------------------------------------------------
# Branded emails
# ---------------------------------------------------------------------------


def test_brand_email_from_applies_display_name():
    brand = get_brand_context({"brand": {"product_name": "Acme Pay"}})
    assert brand_email_from(brand, "no-reply@platform.com") == "Acme Pay <no-reply@platform.com>"


def test_brand_email_from_defaults_to_platform_name():
    brand = get_brand_context(None)
    assert brand_email_from(brand, "no-reply@platform.com") == (
        f"{PLATFORM_PRODUCT_NAME} <no-reply@platform.com>"
    )


def test_brand_email_from_leaves_existing_display_name_and_empty():
    brand = get_brand_context({"brand": {"product_name": "Acme Pay"}})
    # Already a "Name <addr>" — left untouched.
    assert brand_email_from(brand, "Ops <ops@x.com>") == "Ops <ops@x.com>"
    # Empty base address — nothing to brand.
    assert brand_email_from(brand, "") == ""


def test_brand_email_from_sanitizes_display_name():
    # A product name with quotes/newlines must not break the From header.
    brand = BrandContext(
        product_name='Ac"me\r\nPay',
        logo_url="",
        accent_color=PLATFORM_ACCENT_COLOR,
        support_url="",
        legal_url="",
    )
    out = brand_email_from(brand, "x@y.com")
    assert '"' not in out
    assert "\n" not in out and "\r" not in out
    assert out.endswith("<x@y.com>")


def test_brand_email_html_header_carries_name_and_accent():
    brand = get_brand_context({"brand": {"product_name": "Acme Pay", "accent_color": "#112233"}})
    header = brand_email_html_header(brand)
    assert "Acme Pay" in header
    assert "#112233" in header


def test_brand_email_footer_only_with_support_url():
    no_support = get_brand_context({"brand": {"product_name": "Acme Pay"}})
    assert brand_email_footer_text(no_support) == ""
    assert brand_email_footer_html(no_support) == ""

    with_support = get_brand_context({"brand": {"support_url": "https://help.acme.test"}})
    assert "help.acme.test" in brand_email_footer_text(with_support)
    assert "help.acme.test" in brand_email_footer_html(with_support)


@pytest.mark.asyncio
async def test_console_adapter_brands_from_and_text(caplog):
    import logging

    from app.services.email_adapters import EmailMessage
    from app.services.email_adapters.console_adapter import ConsoleAdapter

    adapter = ConsoleAdapter({"from_address": "no-reply@platform.com"})
    msg = EmailMessage(
        to="vendor@x.com",
        subject="Invoice paid",
        body_text="Your invoice was paid.",
        brand=get_brand_context(
            {"brand": {"product_name": "Acme Pay", "support_url": "https://help.acme.test"}}
        ),
    )
    with caplog.at_level(logging.INFO):
        await adapter.send(msg)
    logged = caplog.text
    assert "Acme Pay <no-reply@platform.com>" in logged  # branded From
    assert "help.acme.test" in logged  # branded support footer in text


def test_smtp_adapter_branded_mime():
    from app.services.email_adapters import EmailMessage
    from app.services.email_adapters.smtp_adapter import SmtpAdapter

    adapter = SmtpAdapter({"from_address": "no-reply@platform.com"})
    msg = EmailMessage(
        to="vendor@x.com",
        subject="Hello",
        body_text="Body text.",
        body_html="<p>Body html.</p>",
        brand=get_brand_context({"brand": {"product_name": "Acme Pay", "accent_color": "#112233"}}),
    )
    mime = adapter._build(msg)
    assert mime["From"] == "Acme Pay <no-reply@platform.com>"
    # The HTML alternative carries the branded header.
    html_part = mime.get_body(preferencelist=("html",))
    html = html_part.get_content()
    assert "Acme Pay" in html
    assert "#112233" in html
