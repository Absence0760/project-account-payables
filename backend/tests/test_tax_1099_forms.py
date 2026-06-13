"""Tests for 1099-NEC / 1099-MISC PDF generation + masking."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.services.tax_1099 import VendorReportRow
from app.services.tax_1099_forms import (
    FORM_MISC,
    FORM_NEC,
    build_form_context,
    mask_tin,
    render_1099_pdf,
)


def _row(*, ytd: str = "1500.00", name: str = "Acme LLC") -> VendorReportRow:
    return VendorReportRow(
        vendor_id=uuid.uuid4(),
        vendor_name=name,
        tax_id="12-3456789",
        tax_classification="llc_s_corp",
        is_1099_eligible=True,
        w9_received_date=date(2026, 1, 15),
        w9_on_file=True,
        ytd_paid=Decimal(ytd),
        over_threshold=True,
        payment_count=3,
        tin_verified=True,
    )


def test_mask_tin_ein():
    assert mask_tin("12-3456789") == "**-***6789"


def test_mask_tin_ssn():
    assert mask_tin("123-45-6789") == "***-**-6789"


def test_mask_tin_none():
    assert mask_tin(None) is None


def test_build_context_nec_box1():
    ctx = build_form_context(
        row=_row(),
        full_tax_id="12-3456789",
        tax_year=2026,
        form_type=FORM_NEC,
        payer_name="Payer Co",
        payer_tax_id="98-7654321",
        payer_address="1 Main St",
        recipient_address="2 Vendor Rd",
    )
    assert ctx.form_type == FORM_NEC
    assert "Nonemployee compensation" in ctx.box_label
    assert ctx.box_amount == Decimal("1500.00")
    # Context never carries the full TIN — only masked.
    assert ctx.recipient_tin_masked == "**-***6789"
    assert ctx.payer_tin_masked == "**-***4321"


def test_build_context_misc_box():
    ctx = build_form_context(
        row=_row(),
        full_tax_id="12-3456789",
        tax_year=2026,
        form_type=FORM_MISC,
        payer_name="Payer Co",
        payer_tax_id=None,
        payer_address=None,
        recipient_address=None,
        misc_box="1",
    )
    assert ctx.form_type == FORM_MISC
    assert "Rents" in ctx.box_label


def test_build_context_rejects_unknown_form():
    with pytest.raises(ValueError):
        build_form_context(
            row=_row(),
            full_tax_id="12-3456789",
            tax_year=2026,
            form_type="1099-K",
            payer_name="Payer Co",
            payer_tax_id=None,
            payer_address=None,
            recipient_address=None,
        )


def test_render_pdf_produces_valid_pdf_bytes():
    ctx = build_form_context(
        row=_row(),
        full_tax_id="12-3456789",
        tax_year=2026,
        form_type=FORM_NEC,
        payer_name="Payer Co",
        payer_tax_id="98-7654321",
        payer_address="1 Main St",
        recipient_address="2 Vendor Rd",
    )
    pdf = render_1099_pdf(ctx)
    assert isinstance(pdf, bytes)
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 800


def test_render_pdf_does_not_embed_full_tin():
    ctx = build_form_context(
        row=_row(),
        full_tax_id="12-3456789",
        tax_year=2026,
        form_type=FORM_NEC,
        payer_name="Payer Co",
        payer_tax_id="98-7654321",
        payer_address=None,
        recipient_address=None,
    )
    pdf = render_1099_pdf(ctx)
    # The full digit string should not appear uncompressed; reportlab text is
    # not compressed for this small doc, so a substring check is meaningful.
    assert b"123456789" not in pdf
    assert b"987654321" not in pdf


def test_render_pdf_handles_xml_unsafe_vendor_name():
    ctx = build_form_context(
        row=_row(name="<Manufacturing> & Co"),
        full_tax_id="12-3456789",
        tax_year=2026,
        form_type=FORM_NEC,
        payer_name="Payer Co",
        payer_tax_id=None,
        payer_address=None,
        recipient_address=None,
    )
    pdf = render_1099_pdf(ctx)  # must not raise on the angle brackets / amp
    assert pdf.startswith(b"%PDF")
