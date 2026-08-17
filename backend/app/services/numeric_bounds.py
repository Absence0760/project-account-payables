"""Does this ``Decimal`` fit that ``Numeric(precision, scale)`` column?

The API boundary answers this with Pydantic ``max_digits`` / ``decimal_places``
(see ``tests/test_schema_decimal_bounds.py``). The **bulk-intake** paths have no
request schema to hang those on — a CSV row is parsed straight into a ``Decimal``
and handed to the ORM — so they need the same question answered in code, or an
over-range cell reaches the flush and raises ``NumericValueOutOfRangeError``:
a 500 in the middle of an import that has already written earlier rows.

**This mirrors Postgres, and is deliberately laxer than the schema layer.**
Postgres rounds the fraction to the column's scale and *then* range-checks the
integer part, so ``1234.567`` into ``Numeric(15, 2)`` stores ``1234.57`` rather
than failing. :func:`fits_numeric` reproduces exactly that, which means a
too-precise cell is accepted and rounded here while the same value on a JSON
request body is a 422.

That asymmetry is intended. An API client submits one value it authored and can
correct, so silently changing its money is worse than refusing it. A bulk file
is third-party data — a bank statement export, a Day-0 migration from the
customer's old system — where refusing a whole row because the source carried a
third decimal loses far more than it protects. Magnitude is the part that cannot
be rounded away, and magnitude is what these guard.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

#: The shape of almost every money column in the schema (``invoices.amount``,
#: ``corporate_card_transactions.amount``, …).
MONEY_NUMERIC = (15, 2)

#: Statement / reconciliation totals, which are wider (``bank_transactions.amount``,
#: ``vendor_statement_recon_lines.*``, ``positive_pay_files.total_amount``).
STATEMENT_NUMERIC = (18, 2)

#: The three parsers that consume this, and the channel each already had for an
#: unusable cell — the fix routes an over-range value into that channel rather
#: than inventing a new failure mode:
#:
#: * ``csv_import._parse_decimal``            → an ``ImportRowError`` naming the row
#: * ``bank_reconciliation._parse_amount``    → skip the row, PII-free warning
#: * ``vendor_statement_recon.parse_amount``  → the row carries no amount
#:
#: The last is shared by the CSV upload AND the PDF/extraction path, so bounding
#: it once covers both.


def fits_numeric(value: Decimal | None, precision: int, scale: int) -> bool:
    """``True`` when Postgres would store ``value`` in ``Numeric(precision, scale)``.

    ``None`` and any non-finite value (``NaN`` / ``Infinity``, both of which
    ``Decimal("nan")`` and ``Decimal("1e999999")`` can produce from attacker- or
    export-supplied text) are ``False`` — there is nothing to store.
    """
    if value is None or not value.is_finite():
        return False
    try:
        rounded = value.quantize(Decimal(1).scaleb(-scale), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        # The value is so large that quantizing it exceeds the decimal context's
        # precision — comfortably past anything the column could hold.
        return False
    return abs(rounded) < Decimal(10) ** (precision - scale)
