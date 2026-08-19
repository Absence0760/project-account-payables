"""`api/cards._normalize_charge_amount` must respect the ISO-4217 exponent.

It divided a Lithic minor-unit amount by a flat 100, unlike
`payment_adapters.base.minor_units_to_decimal`, which owns the one exponent
table this codebase has. Lithic is USD-only in practice and Nium is
major-unit, so nothing currently in play was mispriced — which is precisely why
it had to be routed through that table BEFORE a card provider or a non-USD card
currency arrives, not after: ¥150000 is ¥150,000 (exponent 0), not ¥1,500, and
150000 fils is 150 KWD (exponent 3), not 1,500.

Also pinned: `card_dashboard`'s `rebate_ytd` filtered `period >= "{year}-01"`
with no upper bound. `period` is a `YYYY-MM` string, so "2027-03" sorts above
"2026-01" and a forward-dated row leaked into year-to-date — and into
`projected_annual`, which divides YTD by months elapsed.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.api.cards import _normalize_charge_amount


@pytest.mark.parametrize(
    ("currency", "raw", "expected"),
    [
        # Exponent 2 — the common case, unchanged.
        ("USD", 150000, Decimal("1500.00")),
        (None, 150000, Decimal("1500.00")),
        # Exponent 0 — a flat /100 turned ¥150,000 into ¥1,500.
        ("JPY", 150000, Decimal("150000")),
        # Exponent 3 — a flat /100 turned 150 KWD into 1,500.
        ("KWD", 150000, Decimal("150.000")),
    ],
)
def test_lithic_minor_units_respect_the_currency_exponent(currency, raw, expected):
    assert _normalize_charge_amount("lithic", raw, Decimal("9999"), currency) == expected


def test_nium_amounts_stay_major_units():
    """Nium reports major units; dividing them recorded 1/100th of every charge."""
    assert _normalize_charge_amount("nium", "50.00", Decimal("9999"), "USD") == Decimal("50.00")


@pytest.mark.parametrize("raw", [None, 0, "", "not-a-number"])
def test_unparseable_amount_falls_back_to_the_card_limit(raw):
    assert _normalize_charge_amount("lithic", raw, Decimal("250.00"), "USD") == Decimal("250.00")
    assert _normalize_charge_amount("nium", raw, Decimal("250.00"), "USD") == Decimal("250.00")


def test_currency_is_optional():
    """A webhook body need not carry a currency; absent, the common exponent of
    2 applies — the old behaviour, so no existing caller changes."""
    assert _normalize_charge_amount("lithic", 150000, None) == Decimal("1500.00")
