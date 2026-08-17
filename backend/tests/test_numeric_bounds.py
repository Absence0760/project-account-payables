"""The bulk-intake half of the over-range-money fix.

``tests/test_schema_decimal_bounds.py`` covers the API boundary, where Pydantic
answers an over-range amount with a 422. The CSV intake paths have no request
schema — a cell is parsed straight into a ``Decimal`` and handed to the ORM — so
an over-range value used to parse cleanly and raise
``NumericValueOutOfRangeError`` at the flush. That is worse than the API case:
the import has already written earlier rows when it blows up.

All three parsers already had a "this cell is unusable" channel (``csv_import``
→ an ``ImportRowError`` naming the row; ``bank_reconciliation`` → skip the row
with a PII-free warning; ``vendor_statement_recon`` → the row carries no
amount), so the fix routes over-range values into the channel that exists
rather than inventing a new failure mode.

Pure Python — no DB, no network.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.bank_reconciliation import _parse_amount
from app.services.csv_import import _parse_decimal
from app.services.numeric_bounds import MONEY_NUMERIC, STATEMENT_NUMERIC, fits_numeric
from app.services.vendor_statement_recon import AMOUNT_CONVENTION_EU, parse_statement_csv
from app.services.vendor_statement_recon import parse_amount as recon_parse_amount

# ---------------------------------------------------------------------------
# fits_numeric — the Postgres rule, reproduced
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,precision,scale,expected",
    [
        # The widest value the column holds, and one integer digit past it.
        ("9999999999999.99", 15, 2, True),
        ("10000000000000.00", 15, 2, False),
        ("-9999999999999.99", 15, 2, True),
        ("-10000000000000.00", 15, 2, False),
        # Postgres ROUNDS the fraction rather than refusing it, so this does too.
        ("1234.567", 15, 2, True),
        ("0.1234567890123456", 15, 2, True),
        # …but rounding can push a value over the edge, and that must be caught.
        ("9999999999999.999", 15, 2, False),
        # Wider and narrower column shapes.
        ("99999999999999999.99", 18, 2, False),
        ("999999999999999.99", 18, 2, True),
        ("99999999.9999", 12, 4, True),
        ("999999999.9999", 12, 4, False),
        ("999.99", 5, 2, True),
        ("1000.00", 5, 2, False),
        ("0", 15, 2, True),
    ],
)
def test_fits_numeric(value, precision, scale, expected):
    assert fits_numeric(Decimal(value), precision, scale) is expected


def test_fits_numeric_rejects_none_and_non_finite():
    """``Decimal("nan")`` and ``Decimal("1e999999")`` are both reachable from
    export- or attacker-supplied text, and neither is storable."""
    assert fits_numeric(None, 15, 2) is False
    assert fits_numeric(Decimal("nan"), 15, 2) is False
    assert fits_numeric(Decimal("Infinity"), 15, 2) is False
    assert fits_numeric(Decimal("-Infinity"), 15, 2) is False


def test_fits_numeric_survives_a_value_too_large_to_quantize():
    """`Decimal.quantize` itself raises `InvalidOperation` once the result would
    exceed the context precision — the guard must answer False, not propagate."""
    assert fits_numeric(Decimal("1E+400"), 15, 2) is False
    assert fits_numeric(Decimal("1E+400"), *MONEY_NUMERIC) is False
    assert fits_numeric(Decimal("1E+400"), *STATEMENT_NUMERIC) is False


# ---------------------------------------------------------------------------
# csv_import — over-range becomes a named bad row, not a 500 mid-import
# ---------------------------------------------------------------------------


def test_csv_parse_decimal_accepts_the_column_maximum():
    assert _parse_decimal("9999999999999.99") == Decimal("9999999999999.99")
    assert _parse_decimal("1,234.56") == Decimal("1234.56")
    assert _parse_decimal("$1,234.56") == Decimal("1234.56")


def test_csv_parse_decimal_rejects_over_range():
    """`invoices.amount` / `corporate_card_transactions.amount` are
    `Numeric(15, 2)`. `None` is what both call sites turn into an
    `ImportRowError`, so the row is named and the rest of the import proceeds."""
    assert _parse_decimal("99999999999999999999.00") is None
    assert _parse_decimal("10000000000000") is None
    assert _parse_decimal("1E+400") is None


def test_csv_parse_decimal_still_rounds_rather_than_refusing_extra_precision():
    """A Day-0 migration from a system carrying more precision must not lose
    whole rows — Postgres rounds the scale, and so this stays permissive."""
    assert _parse_decimal("1234.567") == Decimal("1234.567")


# ---------------------------------------------------------------------------
# bank_reconciliation — one bad line no longer takes down the statement
# ---------------------------------------------------------------------------


def test_bank_parse_amount_accepts_the_column_maximum_and_its_formats():
    assert _parse_amount("999999999999999.99") == Decimal("999999999999999.99")
    assert _parse_amount("(1,234.56)") == Decimal("-1234.56")
    assert _parse_amount("-1234.56") == Decimal("-1234.56")


def test_bank_parse_amount_rejects_over_range():
    """`bank_transactions.amount` is `Numeric(18, 2)`. `None` is the parser's
    existing "bad amount, skipping" signal."""
    assert _parse_amount("9999999999999999999.99") is None
    assert _parse_amount("(9999999999999999999.99)") is None
    assert _parse_amount("1E+400") is None


def test_bank_parse_amount_bound_is_not_tighter_than_the_column():
    """The regression that would matter most: an 18-digit statement total is
    legitimate here even though it would be over-range for a 15-digit money
    column, so the two bounds must not have been conflated."""
    assert _parse_amount("999999999999999.99") is not None
    assert fits_numeric(Decimal("999999999999999.99"), *MONEY_NUMERIC) is False


# ---------------------------------------------------------------------------
# vendor_statement_recon — the shared parser, so one bound covers CSV *and* PDF
# ---------------------------------------------------------------------------


def test_statement_parse_amount_accepts_the_column_maximum_and_its_formats():
    assert recon_parse_amount("999999999999999.99") == Decimal("999999999999999.99")
    assert recon_parse_amount("(1,234.56)") == Decimal("-1234.56")
    assert recon_parse_amount("$1,234.56") == Decimal("1234.56")


def test_statement_parse_amount_rejects_over_range():
    """`vendor_statement_recon_lines.statement_amount` is `Numeric(18, 2)`.
    `None` is the parser's existing "this row carries no amount" signal, which
    both callers already handle — the row is kept when it has an invoice number
    to match on, and dropped when it has neither."""
    assert recon_parse_amount("9999999999999999999.99") is None
    assert recon_parse_amount("(9999999999999999999.99)") is None
    assert recon_parse_amount("1E+400") is None


def test_statement_parse_amount_bound_survives_the_european_convention():
    """The EU reading rewrites the token before the Decimal is built, so the
    bound has to be applied AFTER that rewrite — `1.234.567,89` is 1234567.89,
    not 1.234 — or the check would judge a different number than the one
    returned."""
    assert recon_parse_amount("1.234.567,89", convention=AMOUNT_CONVENTION_EU) == Decimal(
        "1234567.89"
    )
    over_range_eu = "9" * 17 + ".999.999,99"
    assert recon_parse_amount(over_range_eu, convention=AMOUNT_CONVENTION_EU) is None


def test_statement_over_range_row_does_not_take_down_the_whole_csv():
    """The end-to-end shape of the bug: one absurd cell used to abort the run at
    the flush. The row now lands with `amount=None` (still matchable on its
    invoice number) and its siblings parse normally."""
    csv_bytes = (
        b"invoice_number,date,amount\n"
        b"INV-1,2026-01-15,100.00\n"
        b"INV-2,2026-01-16,9999999999999999999.99\n"
        b"INV-3,2026-01-17,250.50\n"
    )
    lines = parse_statement_csv(csv_bytes)
    by_number = {ln.invoice_number: ln for ln in lines}
    assert by_number["INV-1"].amount == Decimal("100.00")
    assert by_number["INV-2"].amount is None
    assert by_number["INV-3"].amount == Decimal("250.50")
