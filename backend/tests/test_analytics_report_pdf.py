"""Branded analytics-report PDF — pure renderer tests.

These need no DB: they feed `AnalyticsReportContext` synthetic rows and assert the
bytes are a real PDF, that a configured brand renders, that a logo-fetch failure
is fail-soft (the export still succeeds, falling back to the product-name text),
and that an unconfigured org falls back to the platform default product name + no
logo. The full HTTP route (both `format=pdf` and the branded `format=csv`
default) is exercised against a real tenant in
`tests/test_cashflow_forecast_api.py` (the analytics-export route's test home).
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

from app.services.analytics_report_pdf import (
    AnalyticsReportContext,
    render_analytics_report_pdf,
)
from app.services.branding import get_brand_context


def _ctx(rows=None, *, brand=None):
    return AnalyticsReportContext(
        title="Invoice Register",
        org_name="Acme & Co <Holdings>",  # exercises XML escaping
        period_label="trailing 90 days",
        generated_at=datetime(2026, 4, 1, 12, 0, tzinfo=UTC),
        header=["invoice_id", "invoice_number", "amount"],
        rows=rows if rows is not None else [["id-1", "INV-001", "123.45"]],
        brand=brand or get_brand_context(None),
    )


# ---------------------------------------------------------------------------
# Pure renderer
# ---------------------------------------------------------------------------


def test_pdf_bytes_start_with_magic():
    pdf = render_analytics_report_pdf(_ctx())
    assert isinstance(pdf, bytes)
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 1000


def test_pdf_empty_rows_still_renders():
    """Zero rows must still produce a valid (cover-only) PDF, not crash."""
    pdf = render_analytics_report_pdf(_ctx(rows=[]))
    assert pdf.startswith(b"%PDF")


def test_pdf_more_rows_grows_document():
    """Proves the rows are actually rendered into the table."""
    empty = render_analytics_report_pdf(_ctx(rows=[]))
    populated = render_analytics_report_pdf(
        _ctx(rows=[[f"id-{i}", f"INV-{i}", "10.00"] for i in range(20)])
    )
    assert populated.startswith(b"%PDF")
    assert len(populated) > len(empty)


def test_pdf_configured_brand_renders():
    """A configured product name + accent must render without error (the
    product-name text header fires because no embeddable logo is set)."""
    brand = get_brand_context({"brand": {"product_name": "Acme Pay", "accent_color": "#112233"}})
    pdf = render_analytics_report_pdf(_ctx(brand=brand))
    assert pdf.startswith(b"%PDF")


def test_pdf_default_brand_when_unconfigured():
    """No brand block → platform default product name, no logo, still renders."""
    brand = get_brand_context(None)
    assert brand.product_name == "FeohLedger"
    assert not brand.has_logo
    pdf = render_analytics_report_pdf(_ctx(brand=brand))
    assert pdf.startswith(b"%PDF")


def test_pdf_logo_fetch_failure_is_fail_soft():
    """A configured logo whose fetch fails must NOT break the export — the
    renderer falls back to the product-name text header and still returns a PDF.
    """
    brand = get_brand_context(
        {"brand": {"product_name": "Acme Pay", "logo_url": "https://cdn.example/logo.png"}}
    )
    # Simulate any fetch failure (timeout / non-2xx / network error).
    with patch("app.services.branding.fetch_logo_bytes", return_value=None) as m:
        pdf = render_analytics_report_pdf(_ctx(brand=brand))
    assert m.called
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 1000
