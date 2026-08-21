"""The card rail's minor-unit conversion must respect the ISO-4217 exponent —
on BOTH halves.

`payment_adapters.base` owns the one exponent table this codebase has, and its
`to_minor_units` / `minor_units_to_decimal` docstrings say the halves must
always move together: fixing one alone turns a symmetric error into a live
mispricing.

* **Read half** — `api/cards._normalize_charge_amount` divided a Lithic
  minor-unit webhook amount by a flat 100. ¥150000 is ¥150,000 (exponent 0),
  not ¥1,500, and 150000 fils is 150 KWD (exponent 3), not 1,500.
* **Write half** — `card_adapters/lithic.create_card` sent
  `int(amount * 100)` as the card's `spend_limit`. Once the read half was
  migrated and the write half was not, the pair was asymmetric in exactly the
  way the base module warns about: a ¥500,000 card was authorized at
  ¥50,000,000 — 100x the payable, spendable by the vendor — while the charge
  that came back was de-scaled correctly, so `card_settlement_block` (which
  compares only our own `amount_limit`) could never see it.

Also pinned: `card_dashboard`'s `rebate_ytd` filtered `period >= "{year}-01"`
with no upper bound. `period` is a `YYYY-MM` string, so "2027-03" sorts above
"2026-01" and a forward-dated row leaked into year-to-date — and into
`projected_annual`, which divides YTD by months elapsed.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest

from app.api.cards import _normalize_charge_amount
from app.services.card_adapters.base import VirtualCardPayload


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


# ------------------------------------------------- the WRITE half (Lithic) ---


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.text = ""

    def json(self):
        return self._payload


class _BodyCapturingClient:
    """httpx.AsyncClient stand-in that records the JSON body of every POST."""

    def __init__(self, sink: dict):
        self._sink = sink

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, *a, **k):
        self._sink["body"] = k.get("json") or {}
        return _FakeResponse(200, {"token": "card_1", "last_four": "4242"})


def _payload(amount: Decimal, currency: str) -> VirtualCardPayload:
    return VirtualCardPayload(
        correlation_id="corr-1",
        invoice_id="inv-1",
        vendor_name="Acme Supplies",
        vendor_email=None,
        amount=amount,
        currency=currency,
        idempotency_key="2f8c1f3e-4a6b-4c2d-9e1f-7b3a5c9d0e11",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("amount", "currency", "expected_spend_limit"),
    [
        # Exponent 2 — the common case, unchanged by the fix.
        (Decimal("1234.56"), "USD", 123456),
        # Exponent 0 — a flat *100 authorized ¥50,000,000 for a ¥500,000 card.
        (Decimal("500000"), "JPY", 500000),
        # Exponent 3 — a flat *100 authorized 500 fils for a 5.000 KWD card,
        # declining a legitimate charge.
        (Decimal("5.000"), "KWD", 5000),
    ],
)
async def test_lithic_spend_limit_respects_the_currency_exponent(
    amount, currency, expected_spend_limit
):
    """The card's authorization ceiling is what the vendor can actually spend,
    so the write half has to resolve the same exponent the read half does."""
    from app.services.card_adapters.lithic import LithicAdapter

    adapter = LithicAdapter({"api_key": "test", "sandbox": True})
    sink: dict = {}
    with patch("httpx.AsyncClient", lambda *a, **k: _BodyCapturingClient(sink)):
        result = await adapter.create_card(_payload(amount, currency))

    assert result.success is True
    assert sink["body"]["spend_limit"] == expected_spend_limit


@pytest.mark.asyncio
async def test_lithic_spend_limit_round_trips_through_the_read_half():
    """Submit and parse are exact inverses for the same currency — the property
    that makes a clean settlement impossible to read as a mismatch."""
    from app.services.card_adapters.lithic import LithicAdapter

    adapter = LithicAdapter({"api_key": "test", "sandbox": True})
    for amount, currency in (
        (Decimal("1234.56"), "USD"),
        (Decimal("500000"), "JPY"),
        (Decimal("5.000"), "KWD"),
    ):
        sink: dict = {}
        with patch("httpx.AsyncClient", lambda *a, **k: _BodyCapturingClient(sink)):
            await adapter.create_card(_payload(amount, currency))
        back = _normalize_charge_amount(
            "lithic", sink["body"]["spend_limit"], Decimal("-1"), currency
        )
        assert back == amount
