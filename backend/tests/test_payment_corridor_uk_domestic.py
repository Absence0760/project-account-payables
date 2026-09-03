"""Payment corridor selection — the UK domestic branch (issue #328).

A same-currency GBP payment to a GB vendor stays entirely inside the UK
banking system: sort code + account number, no IBAN, no SWIFT, no FX. Before
this branch existed `pick_corridor` had no GBP/GB case, so such a payment fell
through to the foreign-same-currency `international_wire` return — forcing SWIFT
routing, the 2.5 % international-wire fee anchor and an IBAN demand on money
that never leaves the country. `bacs` / `faster_payments` / `chaps` did not
exist as rails at all.

Pinned here:
  - GBP → GBP / GB (or no country) auto-selects `faster_payments`, no
    FX / SWIFT / IBAN, ~free fee anchor.
  - An explicit `chaps` / `bacs` override on a genuinely UK-domestic
    destination is honoured (nothing defaults to those) and still carries no
    IBAN/SWIFT — GB is in the SEPA *scheme* for EUR, which is irrelevant to a
    GBP payment.
  - A plain `ach` / `wire` default (what `create_payment_run` stamps on every
    line) is NOT honoured for a GBP/GB payment — there is no UK ACH — so it
    falls through to Faster Payments.
  - Cross-currency into GBP still routes international_wire + FX.
"""

from __future__ import annotations

from decimal import Decimal

from app.services.payment_corridor import CORRIDOR_OVERRIDE_FEES, pick_corridor

# ---------------------------------------------------------------------------
# Auto-selection: GBP → GB → Faster Payments.
# ---------------------------------------------------------------------------


def test_gbp_to_gb_auto_selects_faster_payments():
    c = pick_corridor(
        source_currency="GBP",
        target_currency="GBP",
        target_country="GB",
    )
    assert c.method == "faster_payments"
    assert c.requires_fx is False
    assert c.requires_swift is False
    assert c.requires_iban is False
    assert c.expected_fee_pct == Decimal("0")
    assert "domestic" in c.notes.lower()


def test_gbp_with_no_country_defaults_to_uk_domestic():
    """Most invoices upload without an address country; a GBP invoice paid by a
    GBP-home org is overwhelmingly domestic — the currency is the cue, exactly
    like the USD→ACH no-country case."""
    c = pick_corridor(
        source_currency="GBP",
        target_currency="GBP",
        target_country=None,
    )
    assert c.method == "faster_payments"
    assert c.requires_swift is False


def test_gbp_to_non_gb_country_is_not_uk_domestic():
    """GBP to a non-GB country is a genuine cross-border payment — it must not
    pick up the UK-domestic rails."""
    c = pick_corridor(
        source_currency="GBP",
        target_currency="GBP",
        target_country="FR",
    )
    assert c.method != "faster_payments"
    assert c.requires_swift is True


# ---------------------------------------------------------------------------
# Explicit CHAPS / BACS override on a UK-domestic destination.
# ---------------------------------------------------------------------------


def test_explicit_chaps_override_is_honoured_for_gbp_gb():
    """A very-high-value payment above the Faster Payments limit is sent with
    `requested_method="chaps"` — nothing defaults to CHAPS, so it is a real
    choice."""
    c = pick_corridor(
        source_currency="GBP",
        target_currency="GBP",
        target_country="GB",
        requested_method="chaps",
    )
    assert c.method == "chaps"
    assert c.requires_fx is False
    assert c.requires_swift is False
    assert c.requires_iban is False
    assert c.expected_fee_pct == CORRIDOR_OVERRIDE_FEES["chaps"]


def test_explicit_bacs_override_is_honoured_for_gbp_gb():
    c = pick_corridor(
        source_currency="GBP",
        target_currency="GBP",
        target_country="GB",
        requested_method="bacs",
    )
    assert c.method == "bacs"
    assert c.requires_swift is False
    assert c.requires_iban is False


def test_explicit_faster_payments_override_is_honoured():
    c = pick_corridor(
        source_currency="GBP",
        target_currency="GBP",
        target_country="GB",
        requested_method="faster_payments",
    )
    assert c.method == "faster_payments"
    assert c.requires_iban is False


def test_padded_uk_rail_override_is_normalised():
    for raw in (" chaps ", "CHAPS", "\tChaps\n"):
        c = pick_corridor(
            source_currency="GBP",
            target_currency="GBP",
            target_country="GB",
            requested_method=raw,
        )
        assert c.method == "chaps"
        assert c.requires_iban is False


# ---------------------------------------------------------------------------
# A plain domestic default is NOT a UK rail choice.
# ---------------------------------------------------------------------------


def test_defaulted_ach_for_gbp_gb_falls_through_to_faster_payments():
    """`create_payment_run` stamps `method="ach"` on every line regardless of
    currency/country. There is no UK ACH, so that default must not be honoured
    — auto-selection wins and routes to Faster Payments."""
    c = pick_corridor(
        source_currency="GBP",
        target_currency="GBP",
        target_country="GB",
        requested_method="ach",
    )
    assert c.method == "faster_payments"
    assert c.requires_swift is False


def test_defaulted_wire_for_gbp_gb_falls_through_to_faster_payments():
    c = pick_corridor(
        source_currency="GBP",
        target_currency="GBP",
        target_country="GB",
        requested_method="wire",
    )
    assert c.method == "faster_payments"


def test_uk_rail_override_not_honoured_for_a_foreign_gbp_destination():
    """`chaps` only counts as a real choice on a genuinely GBP/GB destination —
    asked for on a cross-border GBP payment it falls through to the foreign
    routing, not a domestic rail."""
    c = pick_corridor(
        source_currency="GBP",
        target_currency="GBP",
        target_country="FR",
        requested_method="chaps",
    )
    assert c.method != "chaps"


# ---------------------------------------------------------------------------
# Cross-currency into GBP is still an international wire + FX.
# ---------------------------------------------------------------------------


def test_cross_currency_into_gbp_still_routes_international_wire():
    c = pick_corridor(
        source_currency="USD",
        target_currency="GBP",
        target_country="GB",
    )
    assert c.method == "international_wire"
    assert c.requires_fx is True
    assert c.requires_swift is True
