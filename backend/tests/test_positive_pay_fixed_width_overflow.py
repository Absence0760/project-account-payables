"""Fixed-width Positive Pay: identifiers and money must never be silently changed.

A Positive Pay file is a treasury fraud control — the bank refuses any item that
does not match what we told it we issued. That inverts the usual fixed-width
trade-off: a *confidently wrong* record is worse than a failed export, because
the bank either rejects a cheque we genuinely wrote or clears one for a figure
we never authorized.

Two shipped defects are pinned here (PR #356 / issue #321):

1. ``_amount_cents`` padded then **sliced** (``.rjust(w, "0")[:w]``), keeping the
   HIGH-order digits and dropping the low-order ones. An overrunning amount was
   therefore not merely imprecise — it was divided by ten per dropped digit, so
   a wrong number that still looks like a number reached the bank.
2. The drawee ``account_number`` column was 8 chars against a FULL account
   number from ``settings.payments.check_account_number`` (US accounts run 8-12
   digits), so *every* rendered check-issue file already carried a truncated
   account, pointing the record at an account that does not exist. Widened to
   17; the record is consequently 89 chars.

Both now raise :class:`PositivePayFieldOverflow`, surfaced by the router as a
422 naming the column and its width only — never the offending value, which is
a full account / routing number (PII invariant).

Coverage here goes beyond the landed happy path:

* every identifier column that can overrun (check number, drawee account, ACH
  routing, ACH account) at width, one over, and well over;
* the amount as a **round-trip invariant** — a rendered amount always decodes
  back to the exact input, so no future re-slice can reintroduce a rescale;
* the alignment / padding / record-length contract the layout documents;
* a source-level drift guard that no identifier column is wired to the
  truncating text helper;
* the HTTP surface: 422 (not 500), a PII-free body, and **no** ``PositivePayFile``
  row or MinIO object left behind by the refused export;
* the ``csv`` formatter as the non-regression control — it must still render
  everything it can legitimately represent.

See ``backend/docs/positive-pay.md`` § Field overflow.
"""

from __future__ import annotations

import csv
import datetime
import inspect
import io
import re
import uuid
from datetime import UTC, date, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select

from app.models.entity import Entity
from app.models.invoice import Invoice, InvoiceStatus
from app.models.organization import Organization
from app.models.payment import Payment, PaymentRun
from app.models.positive_pay import PositivePayFile
from app.models.vendor import Vendor
from app.models.workflow import AuditLog
from app.services import storage
from app.services.positive_pay_adapters import (
    AchAuthorizationItem,
    CheckIssueItem,
    FormatterContext,
    PositivePayFieldOverflow,
)
from app.services.positive_pay_adapters import fixed_width_formatter as fw
from app.services.positive_pay_adapters.csv_formatter import CsvPositivePayFormatter
from app.services.positive_pay_adapters.fixed_width_formatter import (
    FixedWidthPositivePayFormatter,
)

# --------------------------------------------------------------------------- #
# helpers — widths come from the module's own constants so these tests are a
# drift guard on the layout rather than a second copy of it.
# --------------------------------------------------------------------------- #

_CTX = FormatterContext(
    company_name="Acme Corp",
    account_number="0001234567",
    file_date=datetime.date(2026, 6, 19),
    currency="USD",
)

# check_issue column offsets, derived (never hardcoded twice).
_OFF_CHECK = 0
_OFF_PAYEE = _OFF_CHECK + fw._W_CHECK_NUMBER
_OFF_AMOUNT = _OFF_PAYEE + fw._W_PAYEE
_OFF_DATE = _OFF_AMOUNT + fw._W_AMOUNT
_OFF_ACCOUNT = _OFF_DATE + fw._W_ISSUE_DATE
_CHECK_RECORD_LEN = _OFF_ACCOUNT + fw._W_ACCOUNT

# ach_authorization column offsets.
_OFF_VENDOR = 0
_OFF_ROUTING = _OFF_VENDOR + fw._W_VENDOR
_OFF_ACH_ACCOUNT = _OFF_ROUTING + fw._W_ROUTING
_OFF_STATUS = _OFF_ACH_ACCOUNT + fw._W_ACH_ACCOUNT
_ACH_RECORD_LEN = _OFF_STATUS + fw._W_STATUS


def _check(number="1001", payee="Globex Inc", amount="1234.56", account="0001234567", day=15):
    return CheckIssueItem(
        check_number=number,
        payee=payee,
        amount=Decimal(amount),
        issue_date=datetime.date(2026, 6, day),
        account_number=account,
    )


def _ach(vendor="Globex", routing="021000021", account="123456789", status="active"):
    return AchAuthorizationItem(
        vendor_name=vendor, routing_number=routing, account_number=account, status=status
    )


def _fw_check_line(**kwargs) -> str:
    out = FixedWidthPositivePayFormatter().format_check_issue([_check(**kwargs)], _CTX)
    return out.rstrip("\r\n")


def _fw_ach_line(**kwargs) -> str:
    out = FixedWidthPositivePayFormatter().format_ach_authorization([_ach(**kwargs)], _CTX)
    return out.rstrip("\r\n")


def _pre_fix_amount_render(amount: Decimal, width: int = fw._W_AMOUNT) -> str:
    """Exactly what the pre-fix renderer produced: pad, then slice.

    Kept in the test rather than the source so the specific wrong-but-plausible
    string it emitted is nameable, and can be asserted *impossible*.
    """
    cents = int((amount.quantize(Decimal("0.01")) * 100).to_integral_value())
    return str(abs(cents)).rjust(width, "0")[:width]


# --------------------------------------------------------------------------- #
# The layout contract itself
# --------------------------------------------------------------------------- #


def test_documented_column_widths_are_the_ones_in_force():
    """The widths are a contract (module docstring + `docs/positive-pay.md`).

    The drawee account column in particular is 17, not the original 8: it holds
    a FULL account number, so 8 truncated every ordinary US account in every
    file the platform ever rendered.
    """
    assert (fw._W_CHECK_NUMBER, fw._W_PAYEE, fw._W_AMOUNT, fw._W_ISSUE_DATE, fw._W_ACCOUNT) == (
        10,
        40,
        14,
        8,
        17,
    )
    assert (fw._W_VENDOR, fw._W_ROUTING, fw._W_ACH_ACCOUNT, fw._W_STATUS) == (40, 9, 17, 14)
    # The documented record lengths, and the drawee column matching the ACH one
    # (they hold the same kind of value — that is why 17 was chosen).
    assert _CHECK_RECORD_LEN == 89
    assert _ACH_RECORD_LEN == 80
    assert fw._W_ACCOUNT == fw._W_ACH_ACCOUNT


def test_every_check_issue_row_is_exactly_the_record_length():
    fmt = FixedWidthPositivePayFormatter()
    out = fmt.format_check_issue(
        [
            _check("1", "A", "0.01", account="1"),
            _check("1234567890", "X" * 80, "999999999999.99", account="9" * 17),
            _check("42", "", "0.00", account=""),
        ],
        _CTX,
    )
    assert out.endswith("\r\n")
    lines = out.rstrip("\r\n").split("\r\n")
    assert len(lines) == 3
    assert {len(line) for line in lines} == {_CHECK_RECORD_LEN}


def test_every_ach_row_is_exactly_the_record_length():
    fmt = FixedWidthPositivePayFormatter()
    out = fmt.format_ach_authorization(
        [
            _ach("A", "0", "1", status=""),
            _ach("V" * 80, "9" * 9, "9" * 17, status="S" * 40),
        ],
        _CTX,
    )
    lines = out.rstrip("\r\n").split("\r\n")
    assert {len(line) for line in lines} == {_ACH_RECORD_LEN}


def test_empty_item_lists_render_nothing_at_all():
    """Headerless layout: no items means no bytes, not a stray terminator."""
    fmt = FixedWidthPositivePayFormatter()
    assert fmt.format_check_issue([], _CTX) == ""
    assert fmt.format_ach_authorization([], _CTX) == ""


@pytest.mark.parametrize(
    "amount,expected",
    [
        ("0.00", "00000000000000"),
        ("0.01", "00000000000001"),
        ("1.00", "00000000000100"),
        ("1234.56", "00000000123456"),
        ("999999999999.99", "99999999999999"),
    ],
)
def test_amount_is_right_justified_zero_padded_and_digits_only(amount, expected):
    """Right-justified zero-padded cents — no sign, no decimal point, no space.

    Pins the padding DIRECTION as well as the value: a "fix" that left-justified
    the amount would keep the digits but move the implied decimal place, which
    is the same class of error as the old slice.
    """
    field = _fw_check_line(amount=amount)[_OFF_AMOUNT : _OFF_AMOUNT + fw._W_AMOUNT]
    assert field == expected
    assert re.fullmatch(r"\d{14}", field)


def test_identifier_fields_are_left_justified_space_padded():
    line = _fw_check_line(number="1001", account="0001234567")
    assert line[_OFF_CHECK : _OFF_CHECK + fw._W_CHECK_NUMBER] == "1001".ljust(10)
    assert line[_OFF_ACCOUNT : _OFF_ACCOUNT + fw._W_ACCOUNT] == "0001234567".ljust(17)

    ach = _fw_ach_line(routing="021000021", account="123456789")
    assert ach[_OFF_ROUTING : _OFF_ROUTING + fw._W_ROUTING] == "021000021"
    assert ach[_OFF_ACH_ACCOUNT : _OFF_ACH_ACCOUNT + fw._W_ACH_ACCOUNT] == "123456789".ljust(17)


def test_descriptive_text_is_left_justified_and_still_truncates():
    """Payee / vendor name / status are cosmetic — the bank matches on the
    identifiers, so truncation there is the documented layout behaviour and must
    NOT have become an error alongside the identifier guard."""
    short = _fw_check_line(payee="Bob")
    assert short[_OFF_PAYEE : _OFF_PAYEE + fw._W_PAYEE] == "Bob".ljust(40)

    long = _fw_check_line(payee="P" * 100)
    assert long[_OFF_PAYEE : _OFF_PAYEE + fw._W_PAYEE] == "P" * 40
    assert len(long) == _CHECK_RECORD_LEN

    ach = _fw_ach_line(vendor="V" * 100, status="S" * 50)
    assert ach[_OFF_VENDOR : _OFF_VENDOR + fw._W_VENDOR] == "V" * 40
    assert ach[_OFF_STATUS : _OFF_STATUS + fw._W_STATUS] == "S" * 14


# --------------------------------------------------------------------------- #
# Identifier overflow — every column that can overrun, at and over its width
# --------------------------------------------------------------------------- #

# (field name, column width, renderer taking a value of that length)
_ID_FIELDS = [
    ("check_number", fw._W_CHECK_NUMBER, lambda v: _fw_check_line(number=v)),
    ("account_number", fw._W_ACCOUNT, lambda v: _fw_check_line(account=v)),
    ("routing_number", fw._W_ROUTING, lambda v: _fw_ach_line(routing=v)),
    ("account_number", fw._W_ACH_ACCOUNT, lambda v: _fw_ach_line(account=v)),
]
_ID_IDS = ["check.check_number", "check.account_number", "ach.routing_number", "ach.account_number"]


@pytest.mark.parametrize("field,width,render", _ID_FIELDS, ids=_ID_IDS)
def test_identifier_exactly_at_its_width_renders(field, width, render):
    """The guard is an overflow check, not an off-by-one that refuses a value
    which fits exactly."""
    value = "7" * width
    line = render(value)
    assert value in line
    assert len(line) in (_CHECK_RECORD_LEN, _ACH_RECORD_LEN)


@pytest.mark.parametrize("field,width,render", _ID_FIELDS, ids=_ID_IDS)
@pytest.mark.parametrize("over", [1, 2, 20])
def test_identifier_over_its_width_raises_rather_than_truncating(field, width, render, over):
    """One character over is as refused as twenty.

    Pre-fix this cut the value to its first `width` characters: a check number
    the bank can match against nothing (so it rejects a cheque we issued), or an
    account number naming a different account.
    """
    with pytest.raises(PositivePayFieldOverflow) as exc:
        render("7" * (width + over))
    assert exc.value.field == field
    assert exc.value.width == width


@pytest.mark.parametrize("field,width,render", _ID_FIELDS, ids=_ID_IDS)
def test_identifier_overflow_message_never_carries_the_value(field, width, render):
    """These values are full account / routing / cheque numbers and the message
    reaches an HTTP body — it names the column and width only (PII invariant)."""
    value = "98765432109876543210987"[: width + 3]
    with pytest.raises(PositivePayFieldOverflow) as exc:
        render(value)
    message = str(exc.value)
    assert value not in message
    assert value not in repr(exc.value.args)
    assert field in message
    assert str(width) in message


@pytest.mark.parametrize("digits", [8, 9, 10, 11, 12, 16, 17])
def test_the_widened_drawee_column_holds_a_real_us_account_whole(digits):
    """The original 8-char column silently truncated every ordinary US account
    (8-12 digits), so every file pointed the bank at an account that does not
    exist. Each of these must now render intact."""
    account = "1" * digits
    line = _fw_check_line(account=account)
    assert line[_OFF_ACCOUNT : _OFF_ACCOUNT + fw._W_ACCOUNT] == account.ljust(17)


@pytest.mark.parametrize("digits", [18, 20, 34])
def test_a_drawee_account_longer_than_the_column_is_refused(digits):
    """34 is IBAN length — a tenant that pasted one into
    `settings.payments.check_account_number` gets a refusal, not a file whose
    account number is the first 17 characters of an IBAN."""
    with pytest.raises(PositivePayFieldOverflow) as exc:
        _fw_check_line(account="2" * digits)
    assert (exc.value.field, exc.value.width) == ("account_number", 17)


# --------------------------------------------------------------------------- #
# Amount — the rescale failure mode, closed as an invariant
# --------------------------------------------------------------------------- #

_AMOUNTS = [
    "0.00",
    "0.01",
    "0.99",
    "1.00",
    "1234.56",
    "99999.99",
    "1000000.00",
    "99999999999.99",
    "999999999999.98",
    "999999999999.99",  # exactly 14 cent-digits — the largest that fits
    "1000000000000.00",  # 15 cent-digits — overruns
    "1234567890123.45",
    "9999999999999.99",  # Numeric(15, 2) maximum: reachable straight from the DB
    "12345678901234.56",
    "99999999999999999.99",
]


@pytest.mark.parametrize("raw", _AMOUNTS)
def test_a_rendered_amount_always_decodes_back_to_the_exact_input(raw):
    """The invariant that kills the rescale for good: either the amount renders
    and decodes back EXACTLY, or the export is refused. There is no third
    outcome — and in particular no outcome where a rendered figure differs from
    the authorized one.

    Written as a round trip rather than a list of expected strings so a future
    re-introduction of any truncation, at any width, fails here.
    """
    amount = Decimal(raw)
    expected_cents = int((amount.quantize(Decimal("0.01")) * 100).to_integral_value())
    try:
        line = _fw_check_line(amount=raw)
    except PositivePayFieldOverflow as exc:
        assert exc.field == "amount"
        assert exc.width == fw._W_AMOUNT
        # It was refused because it genuinely could not fit — not spuriously.
        assert len(str(expected_cents)) > fw._W_AMOUNT
        return
    field = line[_OFF_AMOUNT : _OFF_AMOUNT + fw._W_AMOUNT]
    assert int(field) == expected_cents
    assert Decimal(int(field)) / 100 == amount.quantize(Decimal("0.01"))


@pytest.mark.parametrize(
    "raw,dropped_digits",
    [
        ("1000000000000.00", 1),
        ("12345678901234.56", 2),
        ("99999999999999999.99", 5),
    ],
)
def test_an_overflowing_amount_can_never_render_the_high_order_truncation(raw, dropped_digits):
    """The sharpest half of the defect: the pre-fix slice kept the HIGH-order
    digits, so the emitted figure was the true one divided by ten per dropped
    digit — a wrong number that still looks like a number, which the bank would
    happily clear.

    Asserts the specific wrong string is unreachable, not merely that "an error
    occurs": it reproduces the old render, proves it decodes to a materially
    different (smaller) amount, and then proves no rendering path emits it.
    """
    amount = Decimal(raw)
    wrong = _pre_fix_amount_render(amount)
    assert len(wrong) == fw._W_AMOUNT

    # The old output was a rescale, not a rounding: exactly a power of ten out.
    true_cents = int((amount.quantize(Decimal("0.01")) * 100).to_integral_value())
    assert int(wrong) * (10**dropped_digits) <= true_cents < (int(wrong) + 1) * (10**dropped_digits)
    assert int(wrong) < true_cents

    with pytest.raises(PositivePayFieldOverflow) as exc:
        _fw_check_line(amount=raw)
    assert exc.value.field == "amount"

    # And nothing else in the layout can emit it either — a whole-file render
    # containing that amount must not exist.
    fmt = FixedWidthPositivePayFormatter()
    with pytest.raises(PositivePayFieldOverflow):
        fmt.format_check_issue([_check("1", "V", "1.00"), _check("2", "V", raw)], _CTX)


def test_the_largest_amount_that_fits_and_the_first_that_does_not():
    """Boundary: one cent decides between a rendered file and a refusal."""
    assert (
        _fw_check_line(amount="999999999999.99")[_OFF_AMOUNT : _OFF_AMOUNT + fw._W_AMOUNT]
        == "9" * 14
    )
    with pytest.raises(PositivePayFieldOverflow):
        _fw_check_line(amount="1000000000000.00")


def test_an_amount_that_only_overflows_once_quantized_is_still_refused():
    """The width check runs on the QUANTIZED cents, so a figure that fits before
    rounding and not after is refused rather than sliced. (999999999999.995
        rounds to 1000000000000.00 = 15 cent-digits.)"""
    rounded = Decimal("999999999999.995").quantize(Decimal("0.01"))
    assert len(str(int(rounded * 100))) == 15  # the premise of the test
    with pytest.raises(PositivePayFieldOverflow) as exc:
        _fw_check_line(amount="999999999999.995")
    assert exc.value.field == "amount"


def test_sub_cent_precision_is_quantized_not_leaked():
    """Fractional cents can't reach a fixed-width cent column — they are
    quantized to 2 places, and the rendered digits still decode exactly."""
    field = _fw_check_line(amount="1.004")[_OFF_AMOUNT : _OFF_AMOUNT + fw._W_AMOUNT]
    assert field == "00000000000100"


def test_a_negative_amount_renders_its_magnitude_and_overflows_on_magnitude():
    """The column is unsigned by contract. The magnitude is what is checked, so
    a large negative can't sneak past the width guard on its minus sign."""
    field = _fw_check_line(amount="-1234.56")[_OFF_AMOUNT : _OFF_AMOUNT + fw._W_AMOUNT]
    assert field == "00000000123456"
    with pytest.raises(PositivePayFieldOverflow) as exc:
        _fw_check_line(amount="-1000000000000.00")
    assert exc.value.field == "amount"


# --------------------------------------------------------------------------- #
# Dates
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "issue_date",
    [
        datetime.date(2026, 6, 15),
        datetime.date(2024, 2, 29),  # leap day
        datetime.date(2026, 1, 1),
        datetime.date(2026, 12, 31),
        datetime.date(1000, 1, 1),
        datetime.date(9999, 12, 31),  # the largest date Python can hold
    ],
)
def test_the_issue_date_column_is_eight_digits_and_round_trips(issue_date):
    """`YYYYMMDD`, exactly 8 characters, zero-padded, for every representable
    date — so the date column can neither overrun its width nor be padded into
    something the bank parses as a different day."""
    item = CheckIssueItem(
        check_number="1",
        payee="V",
        amount=Decimal("1.00"),
        issue_date=issue_date,
        account_number="0001234567",
    )
    line = FixedWidthPositivePayFormatter().format_check_issue([item], _CTX).rstrip("\r\n")
    field = line[_OFF_DATE : _OFF_DATE + fw._W_ISSUE_DATE]
    assert re.fullmatch(r"\d{8}", field), field
    assert datetime.datetime.strptime(field, "%Y%m%d").date() == issue_date
    assert len(line) == _CHECK_RECORD_LEN


# --------------------------------------------------------------------------- #
# Whole-file behaviour + structural drift guards
# --------------------------------------------------------------------------- #


def test_an_overflow_on_a_later_row_aborts_the_whole_file():
    """No partial file: the refusal names the offending row's column and the
    caller gets nothing, so a 300-cheque run can't be sent to the bank with one
    line quietly missing or mangled."""
    fmt = FixedWidthPositivePayFormatter()
    items = [
        _check("1001", "A", "10.00"),
        _check("1002", "B", "20.00", account="9" * 25),
        _check("1003", "C", "30.00"),
    ]
    with pytest.raises(PositivePayFieldOverflow) as exc:
        fmt.format_check_issue(items, _CTX)
    assert exc.value.field == "account_number"
    # The good rows render fine on their own — the refusal was about row 2.
    assert len(fmt.format_check_issue([items[0], items[2]], _CTX).rstrip("\r\n").split("\r\n")) == 2


def test_no_identifier_column_is_wired_to_the_truncating_text_helper():
    """Drift guard on the split the fix introduced: `_ltext` truncates (fine for
    a payee), `_lid` refuses (required for anything the bank matches on). A new
    identifier column wired to `_ltext` would silently reintroduce the defect,
    so the mapping is asserted rather than trusted to review.
    """
    src = inspect.getsource(fw)
    truncating = set(re.findall(r"_ltext\(\s*it\.(\w+)", src))
    refusing = set(re.findall(r"_lid\(\s*it\.(\w+)", src))

    assert truncating == {"payee", "vendor_name", "status"}, (
        f"{truncating - {'payee', 'vendor_name', 'status'}} is rendered with the "
        "TRUNCATING helper; identifiers must use _lid"
    )
    assert refusing == {"check_number", "account_number", "routing_number"}
    assert not (truncating & refusing)
    # And the money column goes through the amount renderer, which owns its own
    # width check.
    assert re.search(r"_amount_cents\(\s*it\.amount", src)


def test_the_column_widths_account_for_the_whole_record():
    """Sum-of-widths == record length, for both layouts. Adding a column without
    updating the documented record length fails here."""
    check_widths = [
        fw._W_CHECK_NUMBER,
        fw._W_PAYEE,
        fw._W_AMOUNT,
        fw._W_ISSUE_DATE,
        fw._W_ACCOUNT,
    ]
    assert sum(check_widths) == _CHECK_RECORD_LEN == len(_fw_check_line())
    ach_widths = [fw._W_VENDOR, fw._W_ROUTING, fw._W_ACH_ACCOUNT, fw._W_STATUS]
    assert sum(ach_widths) == _ACH_RECORD_LEN == len(_fw_ach_line())


def test_the_overflow_error_is_a_valueerror_carrying_field_and_width():
    """The router keys its 422 off this type, and the message is the response
    body — pin the shape."""
    exc = PositivePayFieldOverflow("account_number", 17)
    assert isinstance(exc, ValueError)
    assert (exc.field, exc.width) == ("account_number", 17)
    assert "account_number" in str(exc) and "17" in str(exc)


def test_rendering_is_deterministic_and_pure_across_the_guard():
    """Same items + context → identical bytes (the content hash depends on it),
    and a refused render leaves the items untouched."""
    fmt = FixedWidthPositivePayFormatter()
    items = [_check("1", "A", "1.00"), _check("2", "B", "2.00")]
    assert fmt.format_check_issue(items, _CTX) == fmt.format_check_issue(items, _CTX)

    bad = _check("1", "A", "1.00", account="9" * 30)
    with pytest.raises(PositivePayFieldOverflow):
        fmt.format_check_issue([bad], _CTX)
    assert bad.account_number == "9" * 30


# --------------------------------------------------------------------------- #
# csv formatter — the non-regression control
# --------------------------------------------------------------------------- #


def test_csv_renders_every_value_the_fixed_width_layout_refuses():
    """CSV has no columns to overflow, so the width guard must not have leaked
    into it: everything the fixed-width layout refuses still renders here,
    verbatim and parseable."""
    fmt = CsvPositivePayFormatter()
    out = fmt.format_check_issue(
        [_check("12345678901234", "V", "9999999999999.99", account="9" * 34)], _CTX
    )
    rows = list(csv.reader(io.StringIO(out)))
    assert rows[0] == ["check_number", "payee", "amount", "issue_date", "account_number"]
    assert rows[1] == ["12345678901234", "V", "9999999999999.99", "2026-06-15", "9" * 34]


def test_csv_ach_renders_over_long_routing_and_account():
    fmt = CsvPositivePayFormatter()
    out = fmt.format_ach_authorization([_ach("V", "0210000210", "9" * 34)], _CTX)
    rows = list(csv.reader(io.StringIO(out)))
    assert rows[1] == ["V", "0210000210", "9" * 34, "active"]


def test_csv_amount_is_the_exact_decimal_never_rescaled():
    """The `str(Decimal)` path keeps scale and trailing zeros — the CSV half
    never had the slice, and must not acquire one."""
    fmt = CsvPositivePayFormatter()
    out = fmt.format_check_issue([_check("1", "V", "1000000000000.00")], _CTX)
    assert list(csv.reader(io.StringIO(out)))[1][2] == "1000000000000.00"


# --------------------------------------------------------------------------- #
# HTTP surface — 422, PII-free, and nothing left behind
# --------------------------------------------------------------------------- #

_TODAY = date.today()
_GOOD_ACCOUNT = "0001234567"  # 10 digits: ordinary, and truncated by the old 8-char column
_LONG_ACCOUNT = "123456789012345678"  # 18 digits: one over the widened column


async def _default_entity_id(s):
    return (
        await s.execute(select(Entity.id).where(Entity.is_default.is_(True)).limit(1))
    ).scalar_one()


async def _set_check_account(realdb, org_id, account=_GOOD_ACCOUNT):
    mk = realdb.control_sessionmaker()
    async with mk() as s:
        org = (await s.execute(select(Organization).where(Organization.id == org_id))).scalar_one()
        settings = dict(org.settings or {})
        settings["company"] = {**(settings.get("company") or {}), "name": "Acme Corp"}
        settings["payments"] = {
            **(settings.get("payments") or {}),
            "check_account_number": account,
        }
        org.settings = settings
        await s.commit()


async def _clear_settings(realdb, org_id):
    mk = realdb.control_sessionmaker()
    async with mk() as s:
        org = (await s.execute(select(Organization).where(Organization.id == org_id))).scalar_one()
        settings = dict(org.settings or {})
        settings.pop("payments", None)
        settings.pop("company", None)
        org.settings = settings
        await s.commit()


async def _seed_check_run(mk, org_id, *, reference: str, amount: str, number: str) -> str:
    """One executed payment run with a single cheque payment — the input the
    check-issue endpoint renders."""
    async with mk() as s:
        entity_id = await _default_entity_id(s)
        vendor = Vendor(
            organization_id=org_id, name="Globex Industrial", status="active", entity_id=entity_id
        )
        s.add(vendor)
        await s.flush()
        invoice = Invoice(
            organization_id=org_id,
            entity_id=entity_id,
            invoice_number=number,
            vendor_name="Globex Industrial",
            vendor_id=vendor.id,
            amount=Decimal(amount),
            currency="USD",
            invoice_date=_TODAY,
            due_date=_TODAY + timedelta(days=30),
            status=InvoiceStatus.approved,
        )
        s.add(invoice)
        await s.flush()
        run = PaymentRun(
            organization_id=org_id,
            entity_id=entity_id,
            status="executed",
            total_amount=Decimal(amount),
            executed_at=datetime.datetime.now(UTC),
        )
        s.add(run)
        await s.flush()
        s.add(
            Payment(
                entity_id=entity_id,
                invoice_id=invoice.id,
                payment_run_id=run.id,
                amount=Decimal(amount),
                method="check",
                status="completed",
                reference=reference,
            )
        )
        await s.commit()
        return str(run.id)


async def _add_ach_vendor(mk, org_id, *, name, routing, account):
    async with mk() as s:
        s.add(
            Vendor(
                organization_id=org_id,
                name=name,
                status="active",
                bank_details={"routing_number": routing, "account_number": account},
                entity_id=await _default_entity_id(s),
            )
        )
        await s.commit()


async def _file_rows(mk):
    async with mk() as s:
        return (await s.execute(select(PositivePayFile))).scalars().all()


def _upload_spy():
    """A spy around the real uploader.

    The partial-write question is "did any bytes reach MinIO for a refused
    export?", and the refusal happens before a `file_key` exists — so the
    only observable is whether the uploader was called at all.
    """
    return patch(
        "app.services.storage.upload_positive_pay_file",
        new=AsyncMock(side_effect=storage.upload_positive_pay_file),
    )


async def test_check_issue_overflowing_check_number_is_422_and_persists_nothing(realdb):
    """An 18-char cheque reference against the 10-char column.

    Pre-fix this rendered `CHK-2026-0` — a record the bank matches against
    nothing, so it would refuse a cheque we genuinely issued. Now: 422, no row,
    no object, and the (run, format) idempotency slot stays free.
    """
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    await _set_check_account(realdb, org_id)
    try:
        run_id = await _seed_check_run(
            mk, org_id, reference="CHK-2026-00001234", amount="100.00", number="INV-FWO-1"
        )
        with _upload_spy() as spy:
            async with realdb.client(key="a", role="ap_manager") as c:
                resp = await c.post(
                    f"/api/positive-pay/payment-runs/{run_id}/check-issue",
                    json={"bank_format": "fixed_width"},
                )
                assert resp.status_code == 422, resp.text
                detail = resp.json()["detail"]
                assert "check_number" in detail
                assert "10-character" in detail
                # Never the value itself.
                assert "CHK-2026-00001234" not in resp.text

                # The refusal did not burn the slot: csv still renders the run.
                ok = await c.post(
                    f"/api/positive-pay/payment-runs/{run_id}/check-issue",
                    json={"bank_format": "csv"},
                )
                assert ok.status_code == 201, ok.text
            # Exactly one upload: the csv one that succeeded.
            assert spy.await_count == 1

        rows = await _file_rows(mk)
        assert [r.bank_format for r in rows] == ["csv"]
    finally:
        await _clear_settings(realdb, org_id)


async def test_check_issue_overflowing_drawee_account_is_422_with_no_account_in_the_body(realdb):
    """The org's own `check_account_number` is what overruns here, so the error
    reaches the operator carrying a FULL account number unless it is suppressed
    — the reason `PositivePayFieldOverflow` names the column only."""
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    await _set_check_account(realdb, org_id, account=_LONG_ACCOUNT)
    try:
        run_id = await _seed_check_run(
            mk, org_id, reference="CHK1002", amount="100.00", number="INV-FWO-2"
        )
        with _upload_spy() as spy:
            async with realdb.client(key="a", role="ap_manager") as c:
                resp = await c.post(
                    f"/api/positive-pay/payment-runs/{run_id}/check-issue",
                    json={"bank_format": "fixed_width"},
                )
        assert resp.status_code == 422, resp.text
        body = resp.text
        assert "account_number" in body and "17-character" in body
        assert _LONG_ACCOUNT not in body
        # Not even a fragment of it — no partial account number either.
        assert _LONG_ACCOUNT[:10] not in body
        spy.assert_not_awaited()
        assert await _file_rows(mk) == []
    finally:
        await _clear_settings(realdb, org_id)


async def test_check_issue_overflowing_amount_is_422_never_a_rescaled_file(realdb):
    """`Payment.amount` is `Numeric(15, 2)`, so 9999999999999.99 is a legal DB
    value whose cent representation is 15 digits — one over the column.

    Pre-fix the file said 99999999999999 cents ($999,999,999,999.99), a tenth of
    the authorized figure, and the bank would have cleared that instead. Now the
    export is refused and no file exists to send.
    """
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    await _set_check_account(realdb, org_id)
    try:
        run_id = await _seed_check_run(
            mk, org_id, reference="CHK1003", amount="9999999999999.99", number="INV-FWO-3"
        )
        with _upload_spy() as spy:
            async with realdb.client(key="a", role="ap_manager") as c:
                resp = await c.post(
                    f"/api/positive-pay/payment-runs/{run_id}/check-issue",
                    json={"bank_format": "fixed_width"},
                )
        assert resp.status_code == 422, resp.text
        assert "amount" in resp.json()["detail"]
        assert "14-character" in resp.json()["detail"]
        spy.assert_not_awaited()
        assert await _file_rows(mk) == []
    finally:
        await _clear_settings(realdb, org_id)


async def test_check_issue_fixed_width_renders_the_full_account_end_to_end(realdb):
    """The widened column, proven on the stored bytes.

    Pre-fix the 10-digit account rendered as `00012345` in every fixed-width
    file. The DB row and the audit trail still carry only the last 4 — the file
    is the one place a full account is deliberately emitted.
    """
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    await _set_check_account(realdb, org_id)
    try:
        run_id = await _seed_check_run(
            mk, org_id, reference="CHK1004", amount="1234.56", number="INV-FWO-4"
        )
        async with realdb.client(key="a", role="ap_manager") as c:
            resp = await c.post(
                f"/api/positive-pay/payment-runs/{run_id}/check-issue",
                json={"bank_format": "fixed_width"},
            )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["account_last4"] == _GOOD_ACCOUNT[-4:]
        assert _GOOD_ACCOUNT not in resp.text

        content, _ = await storage.get_file(body["file_key"])
        line = content.decode().rstrip("\r\n")
        assert len(line) == _CHECK_RECORD_LEN
        assert line[_OFF_CHECK : _OFF_CHECK + fw._W_CHECK_NUMBER] == "CHK1004".ljust(10)
        assert line[_OFF_AMOUNT : _OFF_AMOUNT + fw._W_AMOUNT] == "00000000123456"
        assert line[_OFF_ACCOUNT : _OFF_ACCOUNT + fw._W_ACCOUNT] == _GOOD_ACCOUNT.ljust(17)

        async with mk() as s:
            audit = (
                await s.execute(
                    select(AuditLog).where(
                        AuditLog.action == "positive_pay.check_issue_generated",
                        AuditLog.entity_id == uuid.UUID(body["id"]),
                    )
                )
            ).scalar_one()
            assert _GOOD_ACCOUNT not in str(audit.details)
    finally:
        await _clear_settings(realdb, org_id)


async def test_ach_authorization_overflowing_account_is_422_and_writes_nothing(realdb):
    """A vendor's own bank details overrun the 17-char ACH account column: 422,
    and no file row — the bank is never handed a debit authorization pointing at
    a truncated account (which is a DIFFERENT account)."""
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    await _set_check_account(realdb, org_id)
    try:
        await _add_ach_vendor(
            mk, org_id, name="ACH Long Account", routing="021000021", account="9" * 18
        )
        async with mk() as s:
            before = (
                await s.execute(select(func.count()).select_from(PositivePayFile))
            ).scalar() or 0

        with _upload_spy() as spy:
            async with realdb.client(key="a", role="ap_manager") as c:
                resp = await c.post(
                    "/api/positive-pay/ach-authorization", json={"bank_format": "fixed_width"}
                )
        assert resp.status_code == 422, resp.text
        assert "account_number" in resp.json()["detail"]
        assert "9" * 18 not in resp.text
        spy.assert_not_awaited()

        async with mk() as s:
            after = (
                await s.execute(select(func.count()).select_from(PositivePayFile))
            ).scalar() or 0
        assert after == before
    finally:
        await _clear_settings(realdb, org_id)


async def test_ach_authorization_overflowing_routing_is_422(realdb):
    """A routing number is exactly 9 digits; a 10-digit one is data we cannot
    render, not data to trim to 9 (which names a different bank)."""
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    await _set_check_account(realdb, org_id)
    try:
        await _add_ach_vendor(
            mk, org_id, name="ACH Long Routing", routing="0210000210", account="123456789"
        )
        with _upload_spy() as spy:
            async with realdb.client(key="a", role="ap_manager") as c:
                resp = await c.post(
                    "/api/positive-pay/ach-authorization", json={"bank_format": "fixed_width"}
                )
        assert resp.status_code == 422, resp.text
        assert "routing_number" in resp.json()["detail"]
        assert "9-character" in resp.json()["detail"]
        assert "0210000210" not in resp.text
        spy.assert_not_awaited()
        assert await _file_rows(mk) == []
    finally:
        await _clear_settings(realdb, org_id)


async def test_ach_authorization_fixed_width_happy_path_renders_the_full_account(realdb):
    """A 17-char account (the column's limit) renders whole and the record is
    exactly 80 chars."""
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    await _set_check_account(realdb, org_id)
    account = "1" * 17
    try:
        await _add_ach_vendor(mk, org_id, name="ACH Exact", routing="021000021", account=account)
        async with realdb.client(key="a", role="ap_manager") as c:
            resp = await c.post(
                "/api/positive-pay/ach-authorization", json={"bank_format": "fixed_width"}
            )
        assert resp.status_code == 201, resp.text
        content, _ = await storage.get_file(resp.json()["file_key"])
        line = content.decode().rstrip("\r\n")
        assert len(line) == _ACH_RECORD_LEN
        assert line[_OFF_ROUTING : _OFF_ROUTING + fw._W_ROUTING] == "021000021"
        assert line[_OFF_ACH_ACCOUNT : _OFF_ACH_ACCOUNT + fw._W_ACH_ACCOUNT] == account
        assert account not in resp.text
    finally:
        await _clear_settings(realdb, org_id)


async def test_csv_export_still_succeeds_where_fixed_width_refuses(realdb):
    """Non-regression control at the HTTP layer: the same run the fixed-width
    layout refuses exports cleanly as CSV, which can represent every value. The
    guard is a property of one bank layout, not of the feature."""
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    await _set_check_account(realdb, org_id, account=_LONG_ACCOUNT)
    try:
        run_id = await _seed_check_run(
            mk, org_id, reference="CHK-2026-00009999", amount="9999999999999.99", number="INV-FWO-5"
        )
        async with realdb.client(key="a", role="ap_manager") as c:
            refused = await c.post(
                f"/api/positive-pay/payment-runs/{run_id}/check-issue",
                json={"bank_format": "fixed_width"},
            )
            assert refused.status_code == 422, refused.text

            ok = await c.post(
                f"/api/positive-pay/payment-runs/{run_id}/check-issue",
                json={"bank_format": "csv"},
            )
        assert ok.status_code == 201, ok.text
        content, _ = await storage.get_file(ok.json()["file_key"])
        row = list(csv.reader(io.StringIO(content.decode())))[1]
        assert row[0] == "CHK-2026-00009999"
        assert row[2] == "9999999999999.99"
        assert row[4] == _LONG_ACCOUNT
    finally:
        await _clear_settings(realdb, org_id)
