"""Tests for the 1099 reporting service.

Pure-function tests cover the dataclass → dict conversions + threshold
logic. The SQL aggregation path (``build_1099_report`` against real
data) is not covered by any automated test — the outer-join +
aggregate + date-extract query is hard to fake without actually
running SQL. Verify manually against seed data after any change to
the query shape. See ``backend/scripts/seed.py``.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from app.services.tax_1099 import THRESHOLD_USD, Report1099, VendorReportRow


def _row(
    *,
    name: str = "Acme",
    ytd: str = "0",
    eligible: bool = False,
    w9: bool = False,
    classification: str | None = None,
) -> VendorReportRow:
    return VendorReportRow(
        vendor_id=uuid.uuid4(),
        vendor_name=name,
        tax_id="12-3456789",
        tax_classification=classification,
        is_1099_eligible=eligible,
        w9_received_date=date(2026, 1, 15) if w9 else None,
        w9_on_file=w9,
        ytd_paid=Decimal(ytd),
        over_threshold=Decimal(ytd) >= THRESHOLD_USD,
        payment_count=1 if Decimal(ytd) > 0 else 0,
    )


def test_threshold_is_six_hundred():
    assert THRESHOLD_USD == Decimal("600")


def test_row_to_dict_round_trip():
    row = _row(name="Acme", ytd="1500.00", eligible=True, w9=True, classification="llc_s_corp")
    d = row.to_dict()
    assert d["vendor_name"] == "Acme"
    assert d["ytd_paid"] == "1500.00"
    assert d["is_1099_eligible"] is True
    assert d["w9_on_file"] is True
    assert d["w9_received_date"] == "2026-01-15"
    assert d["over_threshold"] is True


def test_summary_counts_eligible_over_threshold():
    report = Report1099(
        year=2026,
        generated_at=date.today(),
        rows=[
            _row(name="A", ytd="500", eligible=True, w9=True),  # under threshold
            _row(name="B", ytd="700", eligible=True, w9=True),  # eligible + over
            _row(name="C", ytd="5000", eligible=True, w9=False),  # eligible + over, no W-9
            _row(name="D", ytd="10000", eligible=False),  # corp, not eligible
        ],
    )
    summary = report.summary()
    assert summary["vendor_count_total"] == 4
    assert summary["vendor_count_eligible_over_threshold"] == 2
    assert summary["vendor_count_over_threshold_without_w9"] == 1
    assert summary["total_reportable_usd"] == "5700"


def test_summary_zero_when_no_eligible_vendors():
    report = Report1099(
        year=2026,
        generated_at=date.today(),
        rows=[_row(name="Corp", ytd="50000", eligible=False)],
    )
    summary = report.summary()
    assert summary["vendor_count_eligible_over_threshold"] == 0
    assert summary["total_reportable_usd"] == "0"


def test_over_threshold_exactly_at_600():
    row = _row(ytd="600")
    assert row.over_threshold is True


def test_under_threshold_at_599_99():
    row = _row(ytd="599.99")
    assert row.over_threshold is False
