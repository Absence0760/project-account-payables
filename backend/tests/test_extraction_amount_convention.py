"""AI-extracted money is read under the document's decimal convention.

``_clean_decimal`` stripped every comma unconditionally, so a vision model
transcribing a European invoice produced silently wrong money with no error
anywhere:

    "850,00"    -> 85000     (a hundredfold OVERSTATEMENT)
    "1.234,56"  -> 1.23456   (a thousandfold UNDERSTATEMENT — it parsed!)
    "12.500,00" -> 12.50000
    "1 234,56"  -> 123456

Nothing downstream caught it: the self-correction pass read the same tokens the
same wrong way, so subtotal + tax still "reconciled" against the mangled total.

`decisions.md` §27 already decided this for supplier statements — the unit that
can answer "is that comma a decimal point?" is the document. The rules now live
in ``services/decimal_convention`` and both readers use them.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.decimal_convention import (
    AMOUNT_CONVENTION_EU,
    AMOUNT_CONVENTION_US,
    apply_convention,
    convention_proved_by,
    detect_convention,
)
from app.services.extraction import (
    _apply_extraction,
    _clean_decimal,
    extraction_amount_convention,
)
from app.services.extraction_adapters.base import (
    ExtractedField,
    ExtractedLineItem,
    ExtractionResult,
)
from app.services.extraction_self_correction import run_self_correction

# --------------------------------------------------------------------------- #
# The shared primitive
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "core,expected",
    [
        ("1,234.56", AMOUNT_CONVENTION_US),  # both separators — rightmost decides
        ("1.234,56", AMOUNT_CONVENTION_EU),
        ("850,00", AMOUNT_CONVENTION_EU),  # lone comma, 2-digit tail
        ("850.5", AMOUNT_CONVENTION_US),
        ("1,234,567", AMOUNT_CONVENTION_US),  # repeated separator = grouping
        ("1.234.567", AMOUNT_CONVENTION_EU),
        ("1,234", None),  # genuinely ambiguous 3-digit tail
        ("1.234", None),
        ("1200", None),  # nothing to prove
        ("1.2.3", None),  # malformed, NOT grouped — must prove nothing
    ],
)
def test_convention_proved_by(core, expected):
    assert convention_proved_by(core) == expected


def test_detect_convention_is_the_documents_answer_and_refuses_contradiction():
    assert detect_convention(["1.234,56", "1.200"]) == AMOUNT_CONVENTION_EU
    assert detect_convention(["1,234.56", "1,200"]) == AMOUNT_CONVENTION_US
    assert detect_convention(["1,234.56", "1.234,56"]) is None
    assert detect_convention(["1200", "850"]) is None


def test_apply_convention_only_moves_the_ambiguous_shape():
    # Self-describing tokens are read on their own terms, even against the doc.
    assert apply_convention("850,00", AMOUNT_CONVENTION_US) == "850.00"
    assert apply_convention("1.234,56", AMOUNT_CONVENTION_US) == "1234.56"
    # The one ambiguous shape follows the document.
    assert apply_convention("1.200", AMOUNT_CONVENTION_EU) == "1200"
    assert apply_convention("1.200", AMOUNT_CONVENTION_US) == "1.200"
    assert apply_convention("1.200", None) == "1.200"  # historical US default


# --------------------------------------------------------------------------- #
# `_clean_decimal` — the regression
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("850,00", "850.00"),  # was 85000
        ("1.234,56", "1234.56"),  # was 1.23456
        ("12.500,00", "12500.00"),  # was 12.50000
        ("1 234,56", "1234.56"),  # French space grouping — was 123456
        ("1\xa0234,56", "1234.56"),  # …and the non-breaking kind
        ("€1.234,56", "1234.56"),
        ("(1.234,56)", "-1234.56"),
        ("-1.234,56", "-1234.56"),
        ("1.234.567,89", "1234567.89"),
    ],
)
def test_european_money_is_no_longer_mangled(raw, expected):
    assert _clean_decimal(raw) == Decimal(expected)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1,234.56", "1234.56"),
        ("$1,234.56", "1234.56"),
        ("1,234,567.89", "1234567.89"),
        ("850.00", "850.00"),
        ("100", "100"),
        ("8.25%", "8.25"),
        ("1,234", "1234"),  # ambiguous — historical US reading kept
    ],
)
def test_us_money_reads_exactly_as_before(raw, expected):
    assert _clean_decimal(raw) == Decimal(expected)


def test_ambiguous_token_follows_the_documents_convention():
    assert _clean_decimal("1.200", AMOUNT_CONVENTION_EU) == Decimal("1200")
    assert _clean_decimal("1,200", AMOUNT_CONVENTION_EU) == Decimal("1.200")
    assert _clean_decimal("1,200", AMOUNT_CONVENTION_US) == Decimal("1200")


def test_malformed_grouping_is_still_unparseable():
    """A tightened grouping rule is what keeps garbage garbage."""
    assert _clean_decimal("1.2.3") is None


# --------------------------------------------------------------------------- #
# Document-level detection, and the two readers agreeing
# --------------------------------------------------------------------------- #


def _eu_result() -> ExtractionResult:
    """A European invoice as a vision model would transcribe it verbatim."""
    return ExtractionResult(
        success=True,
        overall_confidence=0.95,
        amount=ExtractedField("1.234,56", 0.99),
        subtotal=ExtractedField("1.037,45", 0.97),
        tax_amount=ExtractedField("197,11", 0.96),
        invoice_date=ExtractedField("2026-05-01", 0.97),
        due_date=ExtractedField("2026-05-31", 0.95),
        line_items=[
            ExtractedLineItem(
                line_number=1,
                quantity=ExtractedField("1", 0.95),
                unit_price=ExtractedField("1.234,56", 0.97),
                tax=ExtractedField("197,11", 0.93),
                total=ExtractedField("1.234,56", 0.98),
            )
        ],
    )


def test_convention_is_resolved_from_the_whole_money_set():
    assert extraction_amount_convention(_eu_result()) == AMOUNT_CONVENTION_EU


def test_a_us_document_still_resolves_us():
    result = ExtractionResult(
        success=True,
        amount=ExtractedField("1,234.56", 0.99),
        subtotal=ExtractedField("1,037.45", 0.97),
    )
    assert extraction_amount_convention(result) == AMOUNT_CONVENTION_US


def test_apply_extraction_writes_the_right_money_on_a_european_invoice():
    class _Inv:
        pass

    invoice = _Inv()
    _apply_extraction(invoice, _eu_result())
    assert invoice.amount == Decimal("1234.56")
    assert invoice.subtotal == Decimal("1037.45")
    assert invoice.tax_amount == Decimal("197.11")


@pytest.mark.asyncio
async def test_self_correction_reconciles_a_european_invoice():
    """The checker must read the tokens the way the writer did.

    Before this, `1.234,56` was unparseable to the checker, so
    `_check_total_reconciliation` bailed on a `None` amount and the pass
    reported nothing at all — a silent no-op on every EU document.
    """
    report = await run_self_correction(_eu_result())
    assert report.violations == []


@pytest.mark.asyncio
async def test_self_correction_still_catches_a_real_european_mismatch():
    result = _eu_result()
    result.tax_amount = ExtractedField("500,00", 0.96)  # no longer adds up
    report = await run_self_correction(result)
    assert any(v["check"] == "total_reconciliation" for v in report.violations)
