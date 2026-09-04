"""Money-typed annotations for response schemas.

The DB stores money as ``Numeric(15, 2)`` and the workflow / payment
code uses ``Decimal`` throughout, but response schemas used to retype
those values to ``float`` at the API boundary, which collapses any
amount beyond ~$9 quadrillion and reintroduces float rounding for
totals built from many small line items.

These annotations keep the in-Python value as ``Decimal`` (the
``money is exact`` project invariant) while serialising to a JSON
number. The conversion happens once, at JSON-write time.

**The frontend does not type these ``number``**, despite the wire
shape — `frontend/CLAUDE.md` forbids it, because a ``number``-typed
money field invites ``a - b`` / ``Math.max()`` on currency. It types
them ``MoneyAmount`` (``string | number | null``), which is honest
about this serialisation *and* makes raw arithmetic a type error. So
adding a field here does not license a numeric type over there; an
earlier version of this docstring said it did.

For JavaScript clients that need *exact* arithmetic on these values,
switch to ``Decimal | None`` with Pydantic's default string
serialisation. JS ``Number`` is fine for display + comparison up to
roughly 2**53 cents (~$90 trillion); accounts-payable amounts sit
comfortably below that.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from pydantic import PlainSerializer


def _decimal_to_json_number(value: Decimal | None) -> float | None:
    if value is None:
        return None
    # ``float(Decimal)`` is the standard wire-encoding hop. Precision loss
    # only matters above ~2**53 cents, well outside AP territory.
    return float(value)


# Required money field — every payment / invoice amount.
MoneyAmount = Annotated[
    Decimal,
    PlainSerializer(_decimal_to_json_number, return_type=float, when_used="json"),
]

# Optional money field — line-item totals, discount amounts, FX-locked rates
# that are populated only on the international leg.
OptionalMoneyAmount = Annotated[
    Decimal | None,
    PlainSerializer(_decimal_to_json_number, return_type=float | None, when_used="json"),
]
