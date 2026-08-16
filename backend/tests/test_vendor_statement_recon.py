"""Unit tests for the pure vendor-statement reconciliation engine.

The engine (`app.services.vendor_statement_recon`) is pure — no DB, no I/O — so
these are plain pytest functions with no fixtures. They cover:

  * `normalize_invoice_number` canonicalisation
  * `parse_statement_csv` happy path, each `StatementParseError` trigger,
    amount/date format variants, latin-1 fallback, and row-skip rules
  * `reconcile` for every classification, the amount-date fallback, tolerance
    boundary, and the no-double-consume guarantee
  * `ReconSummary` counts + totals
  * `line_unreconciled_amount` per classification incl. None inputs
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.models.vendor_statement_recon import (
    CLASS_AMOUNT_MISMATCH,
    CLASS_MATCHED,
    CLASS_MISSING_OUR_SIDE,
    CLASS_MISSING_THEIR_SIDE,
)
from app.services.vendor_statement_recon import (
    AMOUNT_CONVENTION_EU,
    AMOUNT_CONVENTION_US,
    LedgerInvoice,
    StatementLine,
    StatementParseError,
    detect_amount_convention,
    line_unreconciled_amount,
    normalize_invoice_number,
    parse_amount,
    parse_statement_csv,
    reconcile,
)

# ---------------------------------------------------------------------------
# normalize_invoice_number
# ---------------------------------------------------------------------------


def test_normalize_invoice_number_basic():
    assert normalize_invoice_number("INV-001") == "INV001"
    assert normalize_invoice_number("inv 001") == "INV001"
    assert normalize_invoice_number("#INV001") == "INV001"
    # All three collapse to the same canonical form.
    assert (
        normalize_invoice_number("INV-001")
        == normalize_invoice_number("inv 001")
        == normalize_invoice_number("#INV001")
    )


def test_normalize_invoice_number_blank_and_none():
    assert normalize_invoice_number(None) == ""
    assert normalize_invoice_number("") == ""
    assert normalize_invoice_number("   ") == ""
    assert normalize_invoice_number("---") == ""


# ---------------------------------------------------------------------------
# parse_statement_csv — happy path
# ---------------------------------------------------------------------------


def test_parse_csv_happy_path():
    csv = (
        b"Invoice Number,Invoice Date,Amount,Status\n"
        b"INV-001,2026-01-15,1000.00,open\n"
        b"INV-002,2026-02-20,2500.50,open\n"
    )
    lines = parse_statement_csv(csv)
    assert len(lines) == 2
    assert lines[0].invoice_number == "INV-001"
    assert lines[0].invoice_date == date(2026, 1, 15)
    assert lines[0].amount == Decimal("1000.00")
    assert lines[0].status == "open"
    assert lines[0].raw["Amount"] == "1000.00"
    assert lines[1].invoice_number == "INV-002"
    assert lines[1].amount == Decimal("2500.50")


def test_parse_csv_header_synonyms():
    # 'Ref' + 'Open Balance' + 'Document Date' + 'State' synonyms.
    csv = b"Ref,Document Date,Open Balance,State\nA-1,03/04/2026,500.00,outstanding\n"
    lines = parse_statement_csv(csv)
    assert len(lines) == 1
    assert lines[0].invoice_number == "A-1"
    assert lines[0].invoice_date == date(2026, 3, 4)
    assert lines[0].amount == Decimal("500.00")
    assert lines[0].status == "outstanding"


def test_parse_csv_only_amount_column_ok():
    # No invoice column but an amount column is enough to parse.
    csv = b"Amount\n1000.00\n2000.00\n"
    lines = parse_statement_csv(csv)
    assert len(lines) == 2
    assert lines[0].invoice_number is None
    assert lines[0].amount == Decimal("1000.00")


def test_parse_csv_only_invoice_column_ok():
    # No amount column but an invoice column is enough.
    csv = b"Invoice\nINV-1\nINV-2\n"
    lines = parse_statement_csv(csv)
    assert len(lines) == 2
    assert lines[0].invoice_number == "INV-1"
    assert lines[0].amount is None


# ---------------------------------------------------------------------------
# parse_statement_csv — StatementParseError triggers
# ---------------------------------------------------------------------------


def test_parse_csv_empty_raises():
    with pytest.raises(StatementParseError):
        parse_statement_csv(b"")


def test_parse_csv_header_only_raises():
    with pytest.raises(StatementParseError):
        parse_statement_csv(b"Invoice Number,Amount\n")


def test_parse_csv_missing_required_columns_raises():
    # Neither an invoice-number nor an amount column.
    with pytest.raises(StatementParseError):
        parse_statement_csv(b"Foo,Bar\nbaz,qux\n")


# ---------------------------------------------------------------------------
# parse_statement_csv — format variants + fallback + skip rules
# ---------------------------------------------------------------------------


def test_parse_csv_amount_format_variants():
    csv = b'Invoice,Amount\nA,"1,234.56"\nB,(2.50)\nC,$3000.00\nD,-4.00\n'
    lines = parse_statement_csv(csv)
    amounts = {ln.invoice_number: ln.amount for ln in lines}
    assert amounts["A"] == Decimal("1234.56")
    assert amounts["B"] == Decimal("-2.50")
    assert amounts["C"] == Decimal("3000.00")
    assert amounts["D"] == Decimal("-4.00")


def test_parse_csv_date_format_variants():
    csv = b"Invoice,Date,Amount\nA,2026-01-15,1\nB,01/20/2026,2\nC,2026/03/01,3\n"
    lines = parse_statement_csv(csv)
    by_num = {ln.invoice_number: ln for ln in lines}
    assert by_num["A"].invoice_date == date(2026, 1, 15)
    assert by_num["B"].invoice_date == date(2026, 1, 20)
    assert by_num["C"].invoice_date == date(2026, 3, 1)


def test_parse_csv_dotted_european_date_variants():
    """A dotted date is European-first (``15.01.2026`` = 15 January), while a
    slashed one stays US-first — the separator carries the convention. An
    impossible first reading (month 15) falls through to the other ordering, so
    both still parse."""
    csv = b"Invoice,Date,Amount\nA,15.01.2026,1\nB,01.15.2026,2\nC,2026.03.01,3\nD,03.04.2026,4\n"
    by_num = {ln.invoice_number: ln for ln in parse_statement_csv(csv)}
    assert by_num["A"].invoice_date == date(2026, 1, 15)
    assert by_num["B"].invoice_date == date(2026, 1, 15)
    assert by_num["C"].invoice_date == date(2026, 3, 1)
    # Ambiguous either way; the dotted convention makes it 3 April, not 4 March.
    assert by_num["D"].invoice_date == date(2026, 4, 3)


def test_parse_csv_slashed_date_still_reads_us_first():
    """Guard against the dotted formats disturbing the existing slash order."""
    by_num = {
        ln.invoice_number: ln
        for ln in parse_statement_csv(b"Invoice,Date,Amount\nA,03/04/2026,1\n")
    }
    assert by_num["A"].invoice_date == date(2026, 3, 4)


def test_parse_csv_unparseable_amount_with_number_kept():
    # Bad amount ('-') but a real invoice number → keep, amount None.
    csv = b"Invoice,Amount\nINV-1,-\n"
    lines = parse_statement_csv(csv)
    assert len(lines) == 1
    assert lines[0].invoice_number == "INV-1"
    assert lines[0].amount is None


def test_parse_csv_unparseable_amount_no_number_skipped():
    # Bad amount AND no invoice number → skipped.
    csv = b"Invoice,Amount\n,-\nINV-2,5.00\n"
    lines = parse_statement_csv(csv)
    assert len(lines) == 1
    assert lines[0].invoice_number == "INV-2"


def test_parse_csv_blank_rows_skipped():
    csv = b"Invoice,Amount\nINV-1,1.00\n\n,\nINV-2,2.00\n"
    lines = parse_statement_csv(csv)
    assert [ln.invoice_number for ln in lines] == ["INV-1", "INV-2"]


def test_parse_csv_latin1_fallback():
    # 0xE9 is 'é' in latin-1 but invalid as a lone utf-8 byte.
    csv = "Invoice,Amount\nFACTURÉ-1,10.00\n".encode("latin-1")
    lines = parse_statement_csv(csv)
    assert len(lines) == 1
    assert lines[0].amount == Decimal("10.00")
    assert "FACTUR" in lines[0].invoice_number


def test_parse_csv_utf8_bom():
    csv = b"\xef\xbb\xbfInvoice,Amount\nINV-1,10.00\n"
    lines = parse_statement_csv(csv)
    assert len(lines) == 1
    # The BOM must not contaminate the first header.
    assert lines[0].invoice_number == "INV-1"


# ---------------------------------------------------------------------------
# Decimal convention — detection
# ---------------------------------------------------------------------------


def test_detect_convention_us_from_both_separators():
    assert detect_amount_convention(["1,234.56", "850.00"]) == AMOUNT_CONVENTION_US


def test_detect_convention_eu_from_both_separators():
    assert detect_amount_convention(["1.234,56", "850,00"]) == AMOUNT_CONVENTION_EU


def test_detect_convention_eu_from_a_lone_two_digit_comma_tail():
    """``850,00`` is not valid US formatting — a US thousands group is three
    digits — so a comma with a two-digit tail proves the comma is the decimal
    point, with no other row needed."""
    assert detect_amount_convention(["850,00"]) == AMOUNT_CONVENTION_EU


def test_detect_convention_none_without_separators():
    assert detect_amount_convention(["1200", "850", ""]) is None


def test_detect_convention_none_from_only_ambiguous_three_digit_tails():
    """``1,234`` / ``1.234`` are a thousands group under one convention and a
    three-decimal value under the other. They must not vote, or the resolution
    would be circular."""
    assert detect_amount_convention(["1,234"]) is None
    assert detect_amount_convention(["1.234"]) is None


def test_detect_convention_none_when_document_contradicts_itself():
    assert detect_amount_convention(["1,234.56", "1.234,56"]) is None


def test_detect_convention_ignores_unparseable_values():
    assert detect_amount_convention([None, "", "-", "n/a", "850,00"]) == AMOUNT_CONVENTION_EU


# ---------------------------------------------------------------------------
# Decimal convention — parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        # US, unchanged.
        ("1234.56", "1234.56"),
        ("1,234.56", "1234.56"),
        ("$3000.00", "3000.00"),
        ("(2.50)", "-2.50"),
        ("-4.00", "-4.00"),
        ("1,234,567.89", "1234567.89"),
        # European. `850,00` is the regression: the old unconditional
        # `replace(",", "")` read it as 85000, a hundredfold overstatement.
        ("850,00", "850.00"),
        ("1.234,56", "1234.56"),
        ("1.234.567,89", "1234567.89"),
        ("(1.234,56)", "-1234.56"),
        ("-1.234,56", "-1234.56"),
        ("€1.234,56", "1234.56"),
        # French grouping uses a space (and often a non-breaking one).
        ("1 234,56", "1234.56"),
        ("1\xa0234,56", "1234.56"),
        # No separator at all — identical under either convention.
        ("1200", "1200"),
    ],
)
def test_parse_amount_reads_both_conventions_without_a_document_hint(raw, expected):
    assert parse_amount(raw) == Decimal(expected)


@pytest.mark.parametrize("raw", [None, "", "   ", "-", "n/a", "abc"])
def test_parse_amount_returns_none_for_unparseable(raw):
    assert parse_amount(raw) is None


def test_parse_amount_three_digit_tail_follows_the_document_convention():
    """The one genuinely ambiguous shape, and the only thing `convention` moves."""
    assert parse_amount("1.200", convention=AMOUNT_CONVENTION_EU) == Decimal("1200")
    assert parse_amount("1,200", convention=AMOUNT_CONVENTION_EU) == Decimal("1.200")
    assert parse_amount("1,200", convention=AMOUNT_CONVENTION_US) == Decimal("1200")
    assert parse_amount("1.200", convention=AMOUNT_CONVENTION_US) == Decimal("1.200")


def test_parse_amount_three_digit_tail_defaults_to_us_without_a_convention():
    """Back-compat: this is exactly what the function did before."""
    assert parse_amount("1,200") == Decimal("1200")
    assert parse_amount("1.200") == Decimal("1.200")


def test_parse_amount_self_describing_token_beats_a_contradicting_convention():
    """A document-level vote must not drag a token that says what it is onto
    the wrong reading — otherwise one odd row poisons the whole statement."""
    assert parse_amount("850,00", convention=AMOUNT_CONVENTION_US) == Decimal("850.00")
    assert parse_amount("850.00", convention=AMOUNT_CONVENTION_EU) == Decimal("850.00")
    assert parse_amount("1.234,56", convention=AMOUNT_CONVENTION_US) == Decimal("1234.56")


# ---------------------------------------------------------------------------
# Decimal convention — end to end through the CSV parser
# ---------------------------------------------------------------------------


def test_parse_csv_european_statement():
    csv = b'Invoice,Amount\nA,"1.234,56"\nB,"850,00"\nC,"1.200"\n'
    amounts = {ln.invoice_number: ln.amount for ln in parse_statement_csv(csv)}
    assert amounts["A"] == Decimal("1234.56")
    assert amounts["B"] == Decimal("850.00")
    # Ambiguous on its own; the sibling rows prove the document is European.
    assert amounts["C"] == Decimal("1200")


def test_parse_csv_lone_european_amount_is_not_inflated_hundredfold():
    """The reported defect, end to end: `850,00` reconciled as 85000."""
    lines = parse_statement_csv(b'Invoice,Amount\nINV-1,"850,00"\n')
    assert lines[0].amount == Decimal("850.00")


def test_parse_csv_us_statement_unaffected_by_the_convention_pass():
    csv = b'Invoice,Amount\nA,"1,234.56"\nB,"1,200"\nC,850.00\n'
    amounts = {ln.invoice_number: ln.amount for ln in parse_statement_csv(csv)}
    assert amounts["A"] == Decimal("1234.56")
    assert amounts["B"] == Decimal("1200")
    assert amounts["C"] == Decimal("850.00")


def test_parse_csv_short_rows_do_not_break_convention_detection():
    """A ragged row (fewer cells than headers) must not raise while the amount
    column is being scanned ahead of the main loop."""
    csv = b'Invoice,Amount\nA,"850,00"\nB\n'
    amounts = {ln.invoice_number: ln.amount for ln in parse_statement_csv(csv)}
    assert amounts["A"] == Decimal("850.00")


# ---------------------------------------------------------------------------
# reconcile — helpers
# ---------------------------------------------------------------------------


def _ledger(num, amount, inv_date=None, currency="USD", status="approved"):
    return LedgerInvoice(
        id=uuid.uuid4(),
        invoice_number=num,
        amount=Decimal(amount),
        invoice_date=inv_date,
        currency=currency,
        status=status,
    )


def _stmt(num, amount, inv_date=None, status="open"):
    return StatementLine(
        invoice_number=num,
        invoice_date=inv_date,
        amount=Decimal(amount) if amount is not None else None,
        status=status,
        raw={},
    )


# ---------------------------------------------------------------------------
# reconcile — classifications
# ---------------------------------------------------------------------------


def test_reconcile_exact_match():
    ledger = [_ledger("INV-001", "1000.00")]
    stmt = [_stmt("INV-001", "1000.00")]
    results, summary = reconcile(stmt, ledger)
    assert len(results) == 1
    r = results[0]
    assert r.classification == CLASS_MATCHED
    assert r.match_method == "invoice_number"
    assert r.matched_invoice_id == ledger[0].id
    assert r.ledger_amount == Decimal("1000.00")
    assert r.amount_difference == Decimal("0.00")
    assert summary.matched_count == 1


def test_reconcile_within_tolerance_match():
    # 1 cent difference is within the default 0.01 tolerance.
    ledger = [_ledger("INV-001", "1000.00")]
    stmt = [_stmt("INV-001", "1000.01")]
    results, _ = reconcile(stmt, ledger)
    assert results[0].classification == CLASS_MATCHED
    assert results[0].amount_difference == Decimal("0.01")


def test_reconcile_amount_mismatch_beyond_tolerance():
    ledger = [_ledger("INV-001", "1000.00")]
    stmt = [_stmt("INV-001", "1050.00")]
    results, summary = reconcile(stmt, ledger)
    r = results[0]
    assert r.classification == CLASS_AMOUNT_MISMATCH
    assert r.amount_difference == Decimal("50.00")
    assert r.matched_invoice_id == ledger[0].id
    assert summary.amount_mismatch_count == 1


def test_reconcile_negative_amount_difference():
    # Statement claims less than our ledger.
    ledger = [_ledger("INV-001", "1000.00")]
    stmt = [_stmt("INV-001", "900.00")]
    results, _ = reconcile(stmt, ledger)
    assert results[0].classification == CLASS_AMOUNT_MISMATCH
    assert results[0].amount_difference == Decimal("-100.00")


def test_reconcile_missing_our_side():
    # Supplier billed it, we have no invoice.
    results, summary = reconcile([_stmt("INV-999", "500.00")], [])
    r = results[0]
    assert r.classification == CLASS_MISSING_OUR_SIDE
    assert r.matched_invoice_id is None
    assert r.ledger_amount is None
    assert r.amount_difference is None
    assert r.match_method is None
    assert r.statement_amount == Decimal("500.00")
    assert summary.missing_our_side_count == 1


def test_reconcile_missing_their_side():
    # We have an open invoice the statement omitted.
    ledger = [_ledger("INV-001", "1000.00")]
    results, summary = reconcile([], ledger)
    r = results[0]
    assert r.classification == CLASS_MISSING_THEIR_SIDE
    assert r.matched_invoice_id == ledger[0].id
    assert r.ledger_amount == Decimal("1000.00")
    assert r.statement_invoice_number is None
    assert r.statement_amount is None
    assert r.amount_difference is None
    assert r.match_method is None
    assert r.raw is None
    assert summary.missing_their_side_count == 1


def test_reconcile_amount_date_fallback_match():
    # Numbers don't line up, but amount is equal and dates are close.
    ledger = [_ledger("OUR-REF-A", "750.00", date(2026, 1, 10))]
    stmt = [_stmt("SUPPLIER-REF-X", "750.00", date(2026, 1, 12))]
    results, _ = reconcile(stmt, ledger)
    r = results[0]
    assert r.classification == CLASS_MATCHED
    assert r.match_method == "amount_date"
    assert r.matched_invoice_id == ledger[0].id


def test_reconcile_amount_date_fallback_out_of_window():
    # Equal amount but dates too far apart → no match.
    ledger = [_ledger("OUR-REF-A", "750.00", date(2026, 1, 1))]
    stmt = [_stmt("SUPPLIER-REF-X", "750.00", date(2026, 2, 1))]
    results, _ = reconcile(stmt, ledger)
    assert results[0].classification == CLASS_MISSING_OUR_SIDE


def test_reconcile_amount_date_fallback_missing_date_qualifies():
    # Amount-equality alone qualifies when a date is missing.
    ledger = [_ledger("OUR-REF-A", "750.00", None)]
    stmt = [_stmt("SUPPLIER-REF-X", "750.00", date(2026, 1, 12))]
    results, _ = reconcile(stmt, ledger)
    assert results[0].classification == CLASS_MATCHED
    assert results[0].match_method == "amount_date"


def test_reconcile_number_match_beats_amount_date():
    # An exact number match wins over an amount-date candidate: the statement
    # amount equals OTHER (1000.00) but differs from INV-001 (900.00), so a
    # number-first engine produces an amount_mismatch against INV-001, NOT a
    # clean match against the equal-amount OTHER.
    ledger = [
        _ledger("INV-001", "900.00", date(2026, 1, 1)),
        _ledger("OTHER", "1000.00", date(2026, 1, 1)),
    ]
    stmt = [_stmt("INV-001", "1000.00", date(2026, 1, 1))]
    results, _ = reconcile(stmt, ledger)
    matched = [r for r in results if r.classification == CLASS_AMOUNT_MISMATCH]
    assert len(matched) == 1
    # Matched the number, NOT the equal-amount OTHER invoice.
    assert matched[0].matched_invoice_id == ledger[0].id
    assert matched[0].match_method == "invoice_number"
    assert matched[0].amount_difference == Decimal("100.00")
    # OTHER stays unconsumed → missing_on_their_side.
    their = [r for r in results if r.classification == CLASS_MISSING_THEIR_SIDE]
    assert len(their) == 1
    assert their[0].matched_invoice_id == ledger[1].id


def test_reconcile_ledger_invoice_not_double_consumed():
    # Two statement lines with the same number; only the first consumes the
    # single ledger invoice, the second is missing_our_side.
    ledger = [_ledger("INV-001", "1000.00")]
    stmt = [_stmt("INV-001", "1000.00"), _stmt("INV-001", "1000.00")]
    results, summary = reconcile(stmt, ledger)
    assert results[0].classification == CLASS_MATCHED
    assert results[1].classification == CLASS_MISSING_OUR_SIDE
    assert summary.matched_count == 1
    assert summary.missing_our_side_count == 1


def test_reconcile_amount_date_does_not_double_consume():
    # First statement line takes the ledger invoice by number; second can't
    # claim it again even via amount-date.
    ledger = [_ledger("INV-001", "500.00", date(2026, 1, 1))]
    stmt = [
        _stmt("INV-001", "500.00", date(2026, 1, 1)),
        _stmt("DIFFERENT", "500.00", date(2026, 1, 1)),
    ]
    results, _ = reconcile(stmt, ledger)
    assert results[0].classification == CLASS_MATCHED
    assert results[1].classification == CLASS_MISSING_OUR_SIDE


def test_reconcile_statement_amount_none_treated_as_zero():
    # A matched line with no statement amount → difference is -ledger_amount.
    ledger = [_ledger("INV-001", "300.00")]
    stmt = [_stmt("INV-001", None)]
    results, _ = reconcile(stmt, ledger)
    r = results[0]
    assert r.classification == CLASS_AMOUNT_MISMATCH
    assert r.amount_difference == Decimal("-300.00")


def test_reconcile_custom_tolerance():
    ledger = [_ledger("INV-001", "1000.00")]
    stmt = [_stmt("INV-001", "1005.00")]
    # Default tolerance → mismatch; a $5 tolerance → matched.
    assert reconcile(stmt, ledger)[0][0].classification == CLASS_AMOUNT_MISMATCH
    results, _ = reconcile(stmt, ledger, amount_tolerance=Decimal("5.00"))
    assert results[0].classification == CLASS_MATCHED


def test_reconcile_custom_date_window():
    ledger = [_ledger("OUR", "750.00", date(2026, 1, 1))]
    stmt = [_stmt("THEIRS", "750.00", date(2026, 1, 20))]
    # 19 days apart — beyond default 5, within a 30-day window.
    assert reconcile(stmt, ledger)[0][0].classification == CLASS_MISSING_OUR_SIDE
    results, _ = reconcile(stmt, ledger, date_window_days=30)
    assert results[0].classification == CLASS_MATCHED


# ---------------------------------------------------------------------------
# reconcile — summary
# ---------------------------------------------------------------------------


def test_reconcile_summary_counts_and_totals():
    ledger = [
        _ledger("INV-001", "1000.00"),  # exact match
        _ledger("INV-002", "2000.00"),  # amount mismatch
        _ledger("INV-003", "3000.00"),  # missing their side (not on statement)
    ]
    stmt = [
        _stmt("INV-001", "1000.00"),  # matched
        _stmt("INV-002", "2100.00"),  # mismatch (+100)
        _stmt("INV-999", "500.00"),  # missing our side
    ]
    results, summary = reconcile(stmt, ledger)

    assert summary.line_count == 4  # 3 statement lines + 1 orphan ledger
    assert summary.matched_count == 1
    assert summary.amount_mismatch_count == 1
    assert summary.missing_our_side_count == 1
    assert summary.missing_their_side_count == 1

    # statement_total = sum over the 3 statement-origin lines.
    assert summary.statement_total == Decimal("3600.00")
    # ledger_total = sum over matched + mismatch ledger amounts (1000 + 2000).
    assert summary.ledger_total == Decimal("3000.00")


def test_reconcile_summary_empty_inputs():
    results, summary = reconcile([], [])
    assert results == []
    assert summary.line_count == 0
    assert summary.statement_total == Decimal("0")
    assert summary.ledger_total == Decimal("0")


# ---------------------------------------------------------------------------
# line_unreconciled_amount
# ---------------------------------------------------------------------------


def test_line_unreconciled_amount_missing_our_side():
    assert line_unreconciled_amount(CLASS_MISSING_OUR_SIDE, Decimal("500.00"), None) == Decimal(
        "500.00"
    )
    # Negative statement amount → absolute value.
    assert line_unreconciled_amount(CLASS_MISSING_OUR_SIDE, Decimal("-500.00"), None) == Decimal(
        "500.00"
    )


def test_line_unreconciled_amount_amount_mismatch():
    assert line_unreconciled_amount(
        CLASS_AMOUNT_MISMATCH, Decimal("2100.00"), Decimal("100.00")
    ) == Decimal("100.00")
    # Negative difference → absolute value.
    assert line_unreconciled_amount(
        CLASS_AMOUNT_MISMATCH, Decimal("900.00"), Decimal("-100.00")
    ) == Decimal("100.00")


def test_line_unreconciled_amount_matched_and_their_side_zero():
    assert line_unreconciled_amount(CLASS_MATCHED, Decimal("1000.00"), Decimal("0")) == Decimal("0")
    assert line_unreconciled_amount(CLASS_MISSING_THEIR_SIDE, None, None) == Decimal("0")


def test_line_unreconciled_amount_none_inputs_never_raise():
    # Missing-our-side with no statement amount → 0, not an exception.
    assert line_unreconciled_amount(CLASS_MISSING_OUR_SIDE, None, None) == Decimal("0")
    # Amount-mismatch with no difference → 0.
    assert line_unreconciled_amount(CLASS_AMOUNT_MISMATCH, None, None) == Decimal("0")
