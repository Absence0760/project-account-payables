"""Edge-case tests for the shared XML helpers in app.services.e_invoice._xml.

``to_decimal`` is the only thing between an untrusted supplier's XML amount
field and a ``Decimal`` stored on ``Invoice.amount``. ``Decimal()`` accepts
``"NaN"`` / ``"Infinity"`` as valid, which would corrupt downstream payment
math — so the helper must reject non-finite values. Locale-grouped values
(``"1,200.00"``) must not silently truncate. These cases are not exercised by
the well-formed fixture XML the UBL/CII parser tests use.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.e_invoice._xml import to_decimal


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # Non-finite — Decimal() accepts these, but they are not money.
        ("NaN", None),
        ("nan", None),
        ("Infinity", None),
        ("-Infinity", None),
        ("Inf", None),
        # Exponential notation IS a valid finite number — preserve it.
        ("1.5E+3", Decimal("1500")),
        ("1.5e3", Decimal("1500")),
        # Locale-grouped value is not a valid Decimal — reject, never truncate.
        ("1,200.00", None),
        # Whitespace-only / empty / None.
        ("  ", None),
        ("", None),
        (None, None),
        # Garbage text.
        ("abc", None),
        ("$100", None),
        # Well-formed numbers still work.
        ("1190.00", Decimal("1190.00")),
        ("-5.25", Decimal("-5.25")),
        ("  42  ", Decimal("42")),
    ],
)
def test_to_decimal_edge_cases(value, expected):
    result = to_decimal(value)
    if expected is None:
        assert result is None
    else:
        assert result == expected
        assert isinstance(result, Decimal)


def test_to_decimal_non_finite_never_reaches_invoice_amount():
    """Pin the invariant: a non-finite parse returns None so it is never
    persisted as a Decimal('NaN') / Decimal('Infinity') on the invoice."""
    for poison in ("NaN", "Infinity", "-Infinity", "sNaN"):
        assert to_decimal(poison) is None


def test_to_decimal_exponential_is_finite_and_equal_to_expanded():
    d = to_decimal("1.5E+3")
    assert d is not None
    assert d.is_finite()
    assert d == Decimal("1500")
