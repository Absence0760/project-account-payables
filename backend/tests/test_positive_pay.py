"""Unit tests for the pure Positive Pay layer.

Two pure halves are covered here (no DB, no fixtures — plain pytest functions):

  * the formatter adapters (``positive_pay_adapters``): CSV + fixed-width
    rendering of check-issue and ACH-authorization files, incl. headers, exact
    decimal amounts, empty lists, the dispatcher default, and its refusal of a
    named-but-unregistered bank layout;
  * the return classifier (``services.positive_pay.classify_presented_items``):
    every classification — ``matched_ok`` / ``amount_mismatch`` (altered) /
    ``not_on_file`` — plus check-number normalisation and the tolerance
    boundary.

The async DB builders live in the same service module but are exercised by the
API tests (``test_positive_pay_api.py``); here we stay pure.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest

from app.services.positive_pay import (
    CLASS_AMOUNT_MISMATCH,
    CLASS_MATCHED_OK,
    CLASS_NOT_ON_FILE,
    IssuedItem,
    PresentedItem,
    classify_presented_items,
    normalize_check_number,
)
from app.services.positive_pay_adapters import (
    AchAuthorizationItem,
    CheckIssueItem,
    FormatterContext,
    PositivePayFieldOverflow,
    UnknownPositivePayFormatError,
    get_positive_pay_formatter,
)
from app.services.positive_pay_adapters.csv_formatter import CsvPositivePayFormatter
from app.services.positive_pay_adapters.fixed_width_formatter import (
    FixedWidthPositivePayFormatter,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_CTX = FormatterContext(
    company_name="Acme Corp",
    account_number="0001234567",
    file_date=datetime.date(2026, 6, 19),
    currency="USD",
)


def _check(number, payee, amount, account="0001234567", day=15):
    return CheckIssueItem(
        check_number=number,
        payee=payee,
        amount=Decimal(amount),
        issue_date=datetime.date(2026, 6, day),
        account_number=account,
    )


def _ach(vendor, routing, account, status="active"):
    return AchAuthorizationItem(
        vendor_name=vendor, routing_number=routing, account_number=account, status=status
    )


# ---------------------------------------------------------------------------
# dispatcher
# ---------------------------------------------------------------------------


def test_dispatcher_default_is_csv():
    assert isinstance(get_positive_pay_formatter(None), CsvPositivePayFormatter)
    assert isinstance(get_positive_pay_formatter("csv"), CsvPositivePayFormatter)


def test_dispatcher_fixed_width():
    assert isinstance(get_positive_pay_formatter("fixed_width"), FixedWidthPositivePayFormatter)


def test_dispatcher_refuses_a_named_unknown_format():
    """A NAMED layout we have no formatter for raises — it never renders `csv`
    under the requested name.

    Positive Pay is a fraud control the bank enforces off the file we hand it.
    The old fallback rendered a CSV body, stored it, stamped the
    `PositivePayFile` row + audit trail with the *requested* format, filed the
    `(run, bank_format)` idempotency slot under it and returned 201 — so a typo
    left the tenant believing Positive Pay was in force on a file its bank
    cannot parse. Same call as `decisions.md` §29 / §36.
    """
    with pytest.raises(UnknownPositivePayFormatError) as exc:
        get_positive_pay_formatter("wells_fargo_xyz")
    # Names the bad value and the real alternatives; bounded, no credential.
    assert exc.value.name == "wells_fargo_xyz"
    assert "csv" in str(exc.value)

    # A config dict naming an unknown layout is refused identically.
    with pytest.raises(UnknownPositivePayFormatError):
        get_positive_pay_formatter({"bank_format": "wells_fargo_xyz"})


def test_dispatcher_bounds_an_absurd_format_name():
    """The echoed name is capped at the column width so an oversized value
    can't bloat a log line or an HTTP body."""
    with pytest.raises(UnknownPositivePayFormatError) as exc:
        get_positive_pay_formatter("x" * 500)
    assert len(exc.value.name) == 30


def test_dispatcher_accepts_config_dict():
    assert isinstance(
        get_positive_pay_formatter({"bank_format": "fixed_width"}),
        FixedWidthPositivePayFormatter,
    )
    # No recognised key → default csv.
    assert isinstance(get_positive_pay_formatter({"other": "x"}), CsvPositivePayFormatter)


def test_formatter_metadata():
    csv_fmt = get_positive_pay_formatter("csv")
    assert csv_fmt.format_name == "csv"
    assert csv_fmt.file_extension == "csv"
    assert csv_fmt.content_type == "text/csv"
    fw = get_positive_pay_formatter("fixed_width")
    assert fw.format_name == "fixed_width"
    assert fw.content_type == "text/plain"


# ---------------------------------------------------------------------------
# CSV formatter — check issue
# ---------------------------------------------------------------------------


def test_csv_check_issue_header_and_rows():
    fmt = CsvPositivePayFormatter()
    out = fmt.format_check_issue(
        [
            _check("1001", "Globex Inc", "1234.56"),
            _check("1002", "Initech", "0.99"),
        ],
        _CTX,
    )
    lines = out.split("\r\n")
    assert lines[0] == "check_number,payee,amount,issue_date,account_number"
    assert lines[1] == "1001,Globex Inc,1234.56,2026-06-15,0001234567"
    assert lines[2] == "1002,Initech,0.99,2026-06-15,0001234567"


def test_csv_check_issue_exact_decimal_amount():
    fmt = CsvPositivePayFormatter()
    # str(Decimal) keeps trailing zeros exactly — no float rounding.
    out = fmt.format_check_issue([_check("1003", "Vendor", "1000.00")], _CTX)
    assert "1000.00" in out


def test_csv_check_issue_quotes_payee_with_comma():
    fmt = CsvPositivePayFormatter()
    out = fmt.format_check_issue([_check("1004", "Smith, Jones & Co", "5.00")], _CTX)
    # RFC-4180 quoting of the embedded comma.
    assert '"Smith, Jones & Co"' in out


def test_csv_check_issue_empty_list_is_header_only():
    fmt = CsvPositivePayFormatter()
    out = fmt.format_check_issue([], _CTX)
    assert out == "check_number,payee,amount,issue_date,account_number\r\n"


def test_csv_check_issue_deterministic():
    fmt = CsvPositivePayFormatter()
    items = [_check("1001", "A", "1.00"), _check("1002", "B", "2.00")]
    assert fmt.format_check_issue(items, _CTX) == fmt.format_check_issue(items, _CTX)


# ---------------------------------------------------------------------------
# CSV formatter — ACH authorization
# ---------------------------------------------------------------------------


def test_csv_ach_header_and_rows():
    fmt = CsvPositivePayFormatter()
    out = fmt.format_ach_authorization(
        [_ach("Globex", "021000021", "123456789")],
        _CTX,
    )
    lines = out.split("\r\n")
    assert lines[0] == "vendor_name,routing_number,account_number,status"
    assert lines[1] == "Globex,021000021,123456789,active"


def test_csv_ach_empty_list_is_header_only():
    fmt = CsvPositivePayFormatter()
    out = fmt.format_ach_authorization([], _CTX)
    assert out == "vendor_name,routing_number,account_number,status\r\n"


# ---------------------------------------------------------------------------
# Fixed-width formatter
# ---------------------------------------------------------------------------


def test_fixed_width_check_issue_widths():
    fmt = FixedWidthPositivePayFormatter()
    item = _check("1001", "Globex Inc", "1234.56", account="12345678")
    out = fmt.format_check_issue([item], _CTX)
    line = out.rstrip("\r\n")
    # 10 + 40 + 14 + 8 + 17 = 89
    assert len(line) == 89
    assert line[0:10] == "1001".ljust(10)
    assert line[10:50] == "Globex Inc".ljust(40)
    # amount as zero-padded cents: 1234.56 -> 123456
    assert line[50:64] == "123456".rjust(14, "0")
    assert line[64:72] == "20260615"
    assert line[72:89] == "12345678".ljust(17)


def test_fixed_width_amount_cents_exact():
    fmt = FixedWidthPositivePayFormatter()
    out = fmt.format_check_issue([_check("9", "V", "1000.00")], _CTX)
    line = out.rstrip("\r\n")
    # 1000.00 -> 100000 cents, zero-padded to 14
    assert line[50:64] == "100000".rjust(14, "0")


def test_fixed_width_truncates_long_payee():
    fmt = FixedWidthPositivePayFormatter()
    long_payee = "X" * 60
    out = fmt.format_check_issue([_check("1", long_payee, "1.00")], _CTX)
    line = out.rstrip("\r\n")
    assert line[10:50] == "X" * 40  # truncated to 40


def test_fixed_width_empty_list_is_empty_string():
    fmt = FixedWidthPositivePayFormatter()
    assert fmt.format_check_issue([], _CTX) == ""
    assert fmt.format_ach_authorization([], _CTX) == ""


def test_fixed_width_ach_widths():
    fmt = FixedWidthPositivePayFormatter()
    out = fmt.format_ach_authorization([_ach("Globex", "021000021", "123456789")], _CTX)
    line = out.rstrip("\r\n")
    # 40 + 9 + 17 + 14 = 80
    assert len(line) == 80
    assert line[0:40] == "Globex".ljust(40)
    assert line[40:49] == "021000021"
    assert line[49:66] == "123456789".ljust(17)
    assert line[66:80] == "active".ljust(14)


def test_fixed_width_deterministic():
    fmt = FixedWidthPositivePayFormatter()
    items = [_check("1", "A", "1.00"), _check("2", "B", "2.00")]
    assert fmt.format_check_issue(items, _CTX) == fmt.format_check_issue(items, _CTX)


# ---------------------------------------------------------------------------
# fixed-width overflow: identifiers and money must never truncate
#
# A Positive Pay file is a fraud control: the bank refuses anything that does
# not match what we told it we issued. A silently-truncated identifier or a
# rescaled amount inverts that control — the bank refuses a cheque we really
# wrote, or clears one for the wrong figure — so an overrun is a hard failure.
# Descriptive text (payee, vendor name, status) still truncates: that is the
# documented fixed-width contract and the bank matches on the identifiers.
# ---------------------------------------------------------------------------


def test_overflowing_check_number_is_refused_not_truncated():
    """The old renderer cut an 11-char check number to its first 10, so the
    bank matched the record against nothing and rejected a cheque we issued."""
    fmt = FixedWidthPositivePayFormatter()
    with pytest.raises(PositivePayFieldOverflow) as exc:
        fmt.format_check_issue([_check("12345678901", "V", "1.00")], _CTX)
    assert exc.value.field == "check_number"
    assert exc.value.width == 10


def test_check_number_exactly_at_the_column_width_still_renders():
    """The guard is an overflow check, not an off-by-one that rejects a value
    which fits exactly."""
    fmt = FixedWidthPositivePayFormatter()
    line = fmt.format_check_issue([_check("1234567890", "V", "1.00")], _CTX).rstrip("\r\n")
    assert line[0:10] == "1234567890"
    assert len(line) == 89


def test_ordinary_us_drawee_account_fits_the_column():
    """The column was 8 chars while the value is a FULL account number, so a
    perfectly ordinary 10-digit account was silently truncated in every file.
    It must now render whole."""
    fmt = FixedWidthPositivePayFormatter()
    line = fmt.format_check_issue([_check("1", "V", "1.00", account="0001234567")], _CTX).rstrip(
        "\r\n"
    )
    assert line[72:89] == "0001234567".ljust(17)


def test_overflowing_drawee_account_is_refused():
    fmt = FixedWidthPositivePayFormatter()
    with pytest.raises(PositivePayFieldOverflow) as exc:
        fmt.format_check_issue([_check("1", "V", "1.00", account="1" * 18)], _CTX)
    assert exc.value.field == "account_number"
    assert exc.value.width == 17


def test_overflowing_amount_is_refused_rather_than_rescaled():
    """The old slice kept HIGH-order digits and dropped the low-order ones, so
    an overrunning amount was not merely imprecise — it was divided by ten per
    dropped digit. Refusing is the only safe answer."""
    fmt = FixedWidthPositivePayFormatter()
    # 14 cent-digits hold up to 999_999_999_999.99; one cent more overruns.
    with pytest.raises(PositivePayFieldOverflow) as exc:
        fmt.format_check_issue([_check("1", "V", "1000000000000.00")], _CTX)
    assert exc.value.field == "amount"
    assert exc.value.width == 14


def test_largest_amount_that_fits_still_renders_exactly():
    fmt = FixedWidthPositivePayFormatter()
    line = fmt.format_check_issue([_check("1", "V", "999999999999.99")], _CTX).rstrip("\r\n")
    assert line[50:64] == "99999999999999"
    assert len(line) == 89


def test_overflowing_ach_routing_and_account_are_refused():
    fmt = FixedWidthPositivePayFormatter()
    with pytest.raises(PositivePayFieldOverflow) as exc:
        fmt.format_ach_authorization([_ach("V", "0210000210", "123")], _CTX)
    assert exc.value.field == "routing_number"

    with pytest.raises(PositivePayFieldOverflow) as exc:
        fmt.format_ach_authorization([_ach("V", "021000021", "1" * 18)], _CTX)
    assert exc.value.field == "account_number"
    assert exc.value.width == 17


def test_descriptive_text_still_truncates():
    """Payee / vendor name / status are cosmetic — truncation there is the
    documented layout contract and must NOT have become an error."""
    fmt = FixedWidthPositivePayFormatter()
    line = fmt.format_check_issue([_check("1", "X" * 60, "1.00")], _CTX).rstrip("\r\n")
    assert line[10:50] == "X" * 40

    ach = fmt.format_ach_authorization(
        [_ach("V" * 60, "021000021", "123", status="S" * 20)], _CTX
    ).rstrip("\r\n")
    assert ach[0:40] == "V" * 40
    assert ach[66:80] == "S" * 14


def test_overflow_message_never_carries_the_offending_value():
    """The values that can overrun are full account / routing / cheque numbers.
    The error names the column and width only — it reaches an HTTP body."""
    fmt = FixedWidthPositivePayFormatter()
    secret_account = "9876543210987654321"
    with pytest.raises(PositivePayFieldOverflow) as exc:
        fmt.format_check_issue([_check("1", "V", "1.00", account=secret_account)], _CTX)
    assert secret_account not in str(exc.value)
    assert "account_number" in str(exc.value)


def test_csv_formatter_is_unaffected_by_the_width_guard():
    """CSV has no fixed columns, so nothing there can overflow — a long check
    number and a huge amount must still render rather than raise."""
    fmt = CsvPositivePayFormatter()
    out = fmt.format_check_issue([_check("12345678901234", "V", "1000000000000.00")], _CTX)
    assert "12345678901234" in out


# ---------------------------------------------------------------------------
# normalize_check_number
# ---------------------------------------------------------------------------


def test_normalize_check_number():
    assert normalize_check_number("1001") == "1001"
    assert normalize_check_number("#1001") == "1001"
    assert normalize_check_number("chk-1001") == "CHK1001"
    assert normalize_check_number("CHK 1001") == "CHK1001"
    assert normalize_check_number("chk-1001") == normalize_check_number("CHK1001")


def test_normalize_check_number_blank_and_none():
    assert normalize_check_number(None) == ""
    assert normalize_check_number("") == ""
    assert normalize_check_number("   ") == ""
    assert normalize_check_number("---") == ""


# ---------------------------------------------------------------------------
# classify_presented_items
# ---------------------------------------------------------------------------


def test_classify_matched_ok():
    issued = [IssuedItem("1001", Decimal("500.00"))]
    presented = [PresentedItem("1001", Decimal("500.00"))]
    result = classify_presented_items(presented, issued)
    assert result.presented_count == 1
    assert result.matched_ok == 1
    assert result.amount_mismatch == 0
    assert result.not_on_file == 0
    r = result.results[0]
    assert r.classification == CLASS_MATCHED_OK
    assert r.matched_check_number == "1001"
    assert r.issued_amount == Decimal("500.00")


def test_classify_amount_mismatch_altered_check():
    # Bank saw a cheque for $5,000 that we issued for $500 — an altered item.
    issued = [IssuedItem("1001", Decimal("500.00"))]
    presented = [PresentedItem("1001", Decimal("5000.00"))]
    result = classify_presented_items(presented, issued)
    assert result.amount_mismatch == 1
    assert result.matched_ok == 0
    r = result.results[0]
    assert r.classification == CLASS_AMOUNT_MISMATCH
    assert r.matched_check_number == "1001"
    assert r.presented_amount == Decimal("5000.00")
    assert r.issued_amount == Decimal("500.00")


def test_classify_not_on_file():
    # Bank saw cheque 9999 — we never wrote it.
    issued = [IssuedItem("1001", Decimal("500.00"))]
    presented = [PresentedItem("9999", Decimal("500.00"))]
    result = classify_presented_items(presented, issued)
    assert result.not_on_file == 1
    r = result.results[0]
    assert r.classification == CLASS_NOT_ON_FILE
    assert r.matched_check_number is None
    assert r.issued_amount is None


def test_classify_check_number_normalization_matches():
    # Presented '#1001' matches issued '1001'.
    issued = [IssuedItem("1001", Decimal("100.00"))]
    presented = [PresentedItem("#1001", Decimal("100.00"))]
    result = classify_presented_items(presented, issued)
    assert result.matched_ok == 1
    assert result.results[0].classification == CLASS_MATCHED_OK


def test_classify_tolerance_boundary_at():
    # Exactly one cent off → still matched_ok (<= tolerance).
    issued = [IssuedItem("1001", Decimal("100.00"))]
    presented = [PresentedItem("1001", Decimal("100.01"))]
    result = classify_presented_items(presented, issued)
    assert result.matched_ok == 1
    assert result.results[0].classification == CLASS_MATCHED_OK


def test_classify_tolerance_boundary_beyond():
    # Two cents off → amount_mismatch.
    issued = [IssuedItem("1001", Decimal("100.00"))]
    presented = [PresentedItem("1001", Decimal("100.02"))]
    result = classify_presented_items(presented, issued)
    assert result.amount_mismatch == 1
    assert result.results[0].classification == CLASS_AMOUNT_MISMATCH


def test_classify_custom_tolerance():
    issued = [IssuedItem("1001", Decimal("100.00"))]
    presented = [PresentedItem("1001", Decimal("105.00"))]
    # Default → mismatch; a $5 tolerance → matched.
    assert classify_presented_items(presented, issued).amount_mismatch == 1
    loose = classify_presented_items(presented, issued, amount_tolerance=Decimal("5.00"))
    assert loose.matched_ok == 1


def test_classify_none_presented_amount_treated_as_zero():
    # No amount reported against a $500 cheque → mismatch (|0 - 500| > tol).
    issued = [IssuedItem("1001", Decimal("500.00"))]
    presented = [PresentedItem("1001", None)]
    result = classify_presented_items(presented, issued)
    assert result.amount_mismatch == 1


def test_classify_blank_check_number_is_not_on_file():
    issued = [IssuedItem("1001", Decimal("500.00"))]
    presented = [PresentedItem(None, Decimal("500.00"))]
    result = classify_presented_items(presented, issued)
    assert result.not_on_file == 1
    assert result.results[0].classification == CLASS_NOT_ON_FILE


def test_classify_mixed_batch_counts():
    issued = [
        IssuedItem("1001", Decimal("500.00")),
        IssuedItem("1002", Decimal("250.00")),
    ]
    presented = [
        PresentedItem("1001", Decimal("500.00")),  # matched_ok
        PresentedItem("1002", Decimal("999.00")),  # amount_mismatch
        PresentedItem("8888", Decimal("10.00")),  # not_on_file
    ]
    result = classify_presented_items(presented, issued)
    assert result.presented_count == 3
    assert result.matched_ok == 1
    assert result.amount_mismatch == 1
    assert result.not_on_file == 1


def test_classify_empty_inputs():
    result = classify_presented_items([], [])
    assert result.presented_count == 0
    assert result.results == []
    assert result.matched_ok == result.amount_mismatch == result.not_on_file == 0
