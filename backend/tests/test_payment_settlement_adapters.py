"""Every payment adapter must report the amount the processor SETTLED.

`WebhookEvent.amount` / `.currency` are the evidence
`services/payment_settlement.verify_settlement` compares against what AP
authorized. An adapter that parses `status` out of a payload but drops the
`amount` sitting in the same dict silently downgrades every settlement on that
rail to `unverified` — which is why this file exists as a per-provider guard
rather than a single happy-path test.

The minor-unit rails (Modern Treasury, Stripe, Increase, Column) must
de-scale with the exact inverse of the `amount * 100` their own
`create_payment` applies, so a round-trip can never manufacture a phantom
mismatch. The major-unit rails (Checkeeper, mock) parse the decimal string
straight through. Dwolla legitimately reports nothing — its event body is a
bare envelope — and that must read as `None`, not as a zero.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from decimal import Decimal

import pytest

from app.services.payment_adapters import PaymentStatus
from app.services.payment_adapters.base import minor_units_to_decimal, parse_amount
from app.services.payment_adapters.checkeeper import CheckeeperAdapter
from app.services.payment_adapters.column import ColumnAdapter
from app.services.payment_adapters.dwolla import DwollaAdapter
from app.services.payment_adapters.increase import IncreaseAdapter
from app.services.payment_adapters.mock_adapter import MockPaymentAdapter
from app.services.payment_adapters.modern_treasury import ModernTreasuryAdapter
from app.services.payment_adapters.stripe_treasury import StripeTreasuryAdapter


def _plain_hmac(secret: bytes, body: bytes) -> str:
    return hmac.new(secret, body, hashlib.sha256).hexdigest()


def _timestamped_hmac(secret: bytes, body: bytes) -> tuple[str, str]:
    ts = str(int(time.time()))
    return ts, hmac.new(secret, f"{ts}.".encode() + body, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# The shared conversion helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (123456, Decimal("1234.56")),
        ("123456", Decimal("1234.56")),
        (0, Decimal("0.00")),
        (1, Decimal("0.01")),
    ],
)
def test_minor_units_round_trip_the_submit_scaling(raw, expected):
    """Exactly inverts the `amount * 100` the minor-unit adapters send."""
    assert minor_units_to_decimal(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "not-a-number", True, False, {}])
def test_minor_units_returns_none_for_anything_unparseable(raw):
    """Fail-open: an absent/garbage figure becomes `unverified` downstream,
    never a zero that would read as a 100%-under-settlement discrepancy."""
    assert minor_units_to_decimal(raw) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1234.56", Decimal("1234.56")), (1234.56, Decimal("1234.56")), (10, Decimal("10.00"))],
)
def test_parse_amount_handles_major_unit_strings_and_numbers(raw, expected):
    assert parse_amount(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "abc", True])
def test_parse_amount_returns_none_for_anything_unparseable(raw):
    assert parse_amount(raw) is None


# ---------------------------------------------------------------------------
# Minor-unit rails
# ---------------------------------------------------------------------------


def test_modern_treasury_webhook_reports_settled_amount():
    adapter = ModernTreasuryAdapter({"api_key": "k", "org_id": "o", "webhook_secret": "s3cret"})
    body = json.dumps(
        {
            "id": "evt_1",
            "event": "payment_order.completed",
            "data": {
                "id": "po_1",
                "status": "completed",
                "amount": 123456,  # minor units
                "currency": "USD",
                "reference_number": "REF-1",
            },
        }
    ).encode()
    event = adapter.parse_webhook({"X-Signature": _plain_hmac(b"s3cret", body)}, body)

    assert event is not None
    assert event.status == PaymentStatus.completed
    assert event.amount == Decimal("1234.56")
    assert event.currency == "USD"


def test_stripe_treasury_webhook_reports_settled_amount():
    adapter = StripeTreasuryAdapter({"api_key": "sk", "webhook_secret": "whsec_test"})
    body = json.dumps(
        {
            "id": "evt_1",
            "type": "treasury.outbound_payment.posted",
            "data": {
                "object": {"id": "obp_1", "status": "posted", "amount": 50000, "currency": "usd"}
            },
        }
    ).encode()
    ts, sig = _timestamped_hmac(b"whsec_test", body)
    event = adapter.parse_webhook({"Stripe-Signature": f"t={ts},v1={sig}"}, body)

    assert event is not None
    assert event.amount == Decimal("500.00")
    # Stripe reports lowercase; the verifier compares case-insensitively.
    assert event.currency == "usd"


def test_increase_webhook_reports_settled_amount():
    adapter = IncreaseAdapter({"api_key": "k", "account_id": "a", "webhook_secret": "wsec"})
    body = json.dumps(
        {
            "id": "evt_1",
            "associated_object": {
                "id": "ach_1",
                "status": "complete",
                "amount": 99900,
                "currency": "USD",
            },
        }
    ).encode()
    ts, sig = _timestamped_hmac(b"wsec", body)
    event = adapter.parse_webhook({"Increase-Webhook-Signature": f"t={ts},v1={sig}"}, body)

    assert event is not None
    assert event.amount == Decimal("999.00")
    assert event.currency == "USD"


def test_column_webhook_reports_settled_amount_from_currency_code():
    """Column's field is `currency_code`, matching its submit body — reading
    `currency` instead would silently drop it on every event."""
    adapter = ColumnAdapter({"api_key": "k", "webhook_secret": "wsec"})
    body = json.dumps(
        {
            "id": "evt_1",
            "type": "transfer.settled",
            "data": {
                "id": "acht_1",
                "status": "settled",
                "amount": 25000,
                "currency_code": "USD",
            },
        }
    ).encode()
    event = adapter.parse_webhook({"Column-Signature": _plain_hmac(b"wsec", body)}, body)

    assert event is not None
    assert event.amount == Decimal("250.00")
    assert event.currency == "USD"


# ---------------------------------------------------------------------------
# Major-unit rails
# ---------------------------------------------------------------------------


def test_checkeeper_webhook_reports_the_printed_face_value():
    adapter = CheckeeperAdapter({"api_key": "k", "webhook_secret": "wsec"})
    body = json.dumps(
        {
            "id": "evt_1",
            "check": {
                "id": "chk_1",
                "status": "cleared",
                "amount": "1500.00",
                "currency": "USD",
                "check_number": "1042",
            },
        }
    ).encode()
    event = adapter.parse_webhook({"X-Checkeeper-Signature": _plain_hmac(b"wsec", body)}, body)

    assert event is not None
    assert event.amount == Decimal("1500.00")
    assert event.currency == "USD"


def test_mock_adapter_reports_amount_when_supplied():
    """Guard rail 7 — the settlement-mismatch branch must be reachable in
    local dev with no processor account."""
    adapter = MockPaymentAdapter({"webhook_secret": "x"})
    body = json.dumps(
        {
            "provider_payment_id": "mock_1",
            "status": "completed",
            "amount": "42.50",
            "currency": "USD",
        }
    ).encode()
    event = adapter.parse_webhook({}, body)

    assert event is not None
    assert event.amount == Decimal("42.50")
    assert event.currency == "USD"


def test_mock_adapter_omits_amount_when_not_supplied():
    """Existing fixtures that don't set an amount stay `unverified`, not a
    phantom zero-amount mismatch."""
    adapter = MockPaymentAdapter({"webhook_secret": "x"})
    body = json.dumps({"provider_payment_id": "mock_2", "status": "completed"}).encode()
    event = adapter.parse_webhook({}, body)

    assert event is not None
    assert event.amount is None
    assert event.currency is None


# ---------------------------------------------------------------------------
# The honest blind spot
# ---------------------------------------------------------------------------


def test_dwolla_webhook_reports_no_amount_and_that_is_not_zero():
    """Dwolla's event body is `{id, topic, resourceId, _links}` — the amount
    is only reachable by following `_links.resource`, which the synchronous
    signature-verification path must not do. `None` (→ `unverified`) is the
    correct answer; a `Decimal("0")` would read as a total under-settlement
    and flag every Dwolla payment."""
    adapter = DwollaAdapter({"client_id": "i", "client_secret": "s", "webhook_secret": "wsec"})
    body = json.dumps(
        {"id": "evt_1", "topic": "transfer_completed", "resourceId": "xfer_1"}
    ).encode()
    event = adapter.parse_webhook({"X-Request-Signature-SHA-256": _plain_hmac(b"wsec", body)}, body)

    assert event is not None
    assert event.status == PaymentStatus.completed
    assert event.amount is None
    assert event.currency is None


def test_missing_amount_key_never_becomes_zero_on_a_minor_unit_rail():
    """Same contract for a provider that simply omitted the field on one
    event — the absence must not read as "settled 0.00"."""
    adapter = ModernTreasuryAdapter({"api_key": "k", "org_id": "o", "webhook_secret": "s3cret"})
    body = json.dumps(
        {
            "id": "evt_2",
            "event": "payment_order.completed",
            "data": {"id": "po_2", "status": "completed"},
        }
    ).encode()
    event = adapter.parse_webhook({"X-Signature": _plain_hmac(b"s3cret", body)}, body)

    assert event is not None
    assert event.amount is None


# ---------------------------------------------------------------------------
# Minor-unit exponent — both legs, or neither
# ---------------------------------------------------------------------------
#
# Every minor-unit adapter used to scale by a flat `* 100` on submit, and
# `minor_units_to_decimal` inverted it the same way. Symmetric, so it could
# never raise a phantom settlement mismatch — but symmetrically WRONG for a
# currency whose ISO-4217 exponent isn't 2. ¥5,000 went out as 500,000 minor
# units (a 100x overpayment) and 5 KWD as 500 fils instead of 5,000 (a 10x
# underpayment), and a genuine scale-off on such a currency read as `matched`.
#
# Fixing one leg alone would have converted a symmetric error into a real
# mispricing, which is why the deferral said both must move together. These
# tests are what hold them together.


@pytest.mark.parametrize(
    ("currency", "expected"),
    [
        ("USD", 2),
        ("usd", 2),  # case-insensitive
        ("EUR", 2),
        ("JPY", 0),
        ("KRW", 0),
        ("CLP", 0),
        ("BHD", 3),
        ("KWD", 3),
        ("OMR", 3),
        ("ZZZ", 2),  # unknown -> the near-universal default
        (None, 2),  # absent -> same
    ],
)
def test_exponent_resolution(currency, expected):
    from app.services.payment_adapters.base import exponent_for

    assert exponent_for(currency) == expected


@pytest.mark.parametrize(
    ("amount", "currency", "expected_minor"),
    [
        (Decimal("19.99"), "USD", 1999),
        (Decimal("5000.00"), "USD", 500000),
        # The headline bugs: a zero-exponent currency was inflated 100x...
        (Decimal("5000"), "JPY", 5000),
        (Decimal("100"), "KRW", 100),
        # ...and a three-exponent currency was deflated 10x.
        (Decimal("5.000"), "KWD", 5000),
        (Decimal("1.500"), "BHD", 1500),
    ],
)
def test_submit_scaling_honours_the_currency(amount, currency, expected_minor):
    from app.services.payment_adapters.base import to_minor_units

    assert to_minor_units(amount, currency) == expected_minor


@pytest.mark.parametrize("currency", ["USD", "EUR", "JPY", "KRW", "KWD", "BHD", "ZZZ"])
@pytest.mark.parametrize("amount", [Decimal("1"), Decimal("100"), Decimal("5000")])
def test_round_trip_is_symmetric_for_every_exponent(currency, amount):
    """The property the settlement verifier depends on: what we send, parsed
    back, is what we sent. A break here is a phantom mismatch on every payment
    in that currency."""
    from app.services.payment_adapters.base import minor_units_to_decimal, to_minor_units

    minor = to_minor_units(amount, currency)
    assert minor_units_to_decimal(minor, currency) == amount


def test_half_up_rounding_is_preserved():
    """A .x5 minor unit must not round *down* — consistent with
    international_payments and the rest of the money path."""
    from app.services.payment_adapters.base import to_minor_units

    assert to_minor_units(Decimal("0.005"), "USD") == 1
    assert to_minor_units(Decimal("0.0005"), "KWD") == 1


def test_parse_without_a_currency_still_assumes_cents():
    """A webhook body that omits the currency must keep parsing rather than
    failing — the caller passes it whenever the payload carries it."""
    from app.services.payment_adapters.base import minor_units_to_decimal

    assert minor_units_to_decimal(1999) == Decimal("19.99")


def test_unparseable_amount_is_still_none():
    """Unchanged fail-open contract: 'no reported amount' is `unverified` to
    the verifier, never evidence of a discrepancy."""
    from app.services.payment_adapters.base import minor_units_to_decimal

    assert minor_units_to_decimal(None, "USD") is None
    assert minor_units_to_decimal(True, "USD") is None
    assert minor_units_to_decimal("not-a-number", "USD") is None
