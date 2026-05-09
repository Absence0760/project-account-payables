"""Tests for the model-output cleaners in `services.extraction`.

These are the funnels every vision-model extraction passes through before
the value lands in the DB. A bug here = a silent data drop. The bugs we
already shipped (and have customer scars from):

- `8.25%` parsed to None because Decimal didn't strip `%`
- `March 15, 2024` parsed to None because date.fromisoformat is strict
- `"string or null"` (model prompt leakage) stored verbatim
- `"ACH"` stored, but dropdown bound to `"ach"` showed empty

Lock the contracts so regressions are loud.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

# ---------- _clean_decimal -------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("100", Decimal("100")),
        ("100.50", Decimal("100.50")),
        ("$1,234.56", Decimal("1234.56")),
        ("  1 000.00  ", Decimal("1000.00")),
        ("8.25%", Decimal("8.25")),  # the tax_rate bug
        ("(123.45)", Decimal("-123.45")),  # accounting parens
        ("\u22122.50", Decimal("-2.50")),  # unicode minus
        ("€500", Decimal("500")),
        ("£99.99", Decimal("99.99")),
        ("¥10000", Decimal("10000")),
        ("-15.00", Decimal("-15.00")),
    ],
)
def test_clean_decimal_parses_real_world_inputs(raw, expected):
    from app.services.extraction import _clean_decimal

    assert _clean_decimal(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "   ",
        "null",
        "None",
        "N/A",
        "n/a",
        "-",
        "—",
        "string or null",
        "abc",
        "$$$",
        "1.2.3",
    ],
)
def test_clean_decimal_returns_none_for_garbage(raw):
    from app.services.extraction import _clean_decimal

    assert _clean_decimal(raw) is None


# ---------- _clean_string -------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Acme Corp", "Acme Corp"),
        ("  spaces  ", "spaces"),
        ("INV-12345", "INV-12345"),
    ],
)
def test_clean_string_passes_real_values(raw, expected):
    from app.services.extraction import _clean_string

    assert _clean_string(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "   ",
        "null",
        "NULL",
        "None",
        "N/A",
        "n/a",
        "string or null",  # exact prompt-leak we've observed
        "string",
        "TBD",
        "unknown",
        "not provided",
        "not specified",
        "—",
        "-",
    ],
)
def test_clean_string_filters_sentinels(raw):
    """Without this, prompt-leak strings end up in DB columns and
    pollute search, vendor matching, and exception flagging."""
    from app.services.extraction import _clean_string

    assert _clean_string(raw) is None


# ---------- _clean_date ---------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("2024-03-15", date(2024, 3, 15)),
        ("2024-03-15 14:30:00", date(2024, 3, 15)),  # ISO with time
        ("3/15/2024", date(2024, 3, 15)),  # US
        ("03/15/2024", date(2024, 3, 15)),
        ("3-15-2024", date(2024, 3, 15)),
        ("2024/03/15", date(2024, 3, 15)),
        ("March 15, 2024", date(2024, 3, 15)),
        ("Mar 15, 2024", date(2024, 3, 15)),
        ("15 March 2024", date(2024, 3, 15)),
        ("15 Mar 2024", date(2024, 3, 15)),
        ("15-Mar-2024", date(2024, 3, 15)),
    ],
)
def test_clean_date_parses_common_formats(raw, expected):
    """Strict ISO-only parsing was silently dropping every non-ISO date
    the model returned. This is THE most common silent-drop bug."""
    from app.services.extraction import _clean_date

    assert _clean_date(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "   ", "null", "N/A", "not a date", "13/45/9999"])
def test_clean_date_returns_none_for_garbage(raw):
    from app.services.extraction import _clean_date

    assert _clean_date(raw) is None


# ---------- _normalize_payment_method -------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("ach", "ach"),
        ("ACH", "ach"),
        ("Ach", "ach"),
        ("ACH Transfer", "ach"),
        ("Automated Clearing House", "ach"),
        ("ACH preferred. Wire transfer accepted.", "ach"),  # exact field text we've seen
        ("Wire", "wire"),
        ("WIRE TRANSFER", "wire"),
        ("Wire Transfer", "wire"),
        ("SWIFT", "wire"),
        ("RTP", "wire"),  # folded — no dropdown option
        ("Check", "check"),
        ("cheque", "check"),
        ("Paper Check", "check"),
        ("Credit Card", "credit_card"),
        ("CC", "credit_card"),
        ("Card", "credit_card"),
        ("Virtual Card", "credit_card"),
    ],
)
def test_normalize_payment_method(raw, expected):
    """Dropdown options are lowercase canonical values; without
    normalisation, `"ACH"` from the model leaves the select empty."""
    from app.services.extraction import _normalize_payment_method

    assert _normalize_payment_method(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "   ", "null", "n/a", "exotic-rail-not-in-our-list"])
def test_normalize_payment_method_returns_none_for_unknown(raw):
    from app.services.extraction import _normalize_payment_method

    assert _normalize_payment_method(raw) is None
