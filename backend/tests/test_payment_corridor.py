"""Payment corridor selection — pick_corridor pure function.

Pins the rules that decide which rails carry a payment given (source
currency, target currency, target country). A regression would
either send money on the wrong network (wire fees on what should be
a free SEPA) or trigger validation failure downstream (the corridor
demands an IBAN but the path didn't flag it).

Rules pinned here:
  - Cross-currency → international_wire, requires_fx + requires_swift
  - Same-currency USD/US → ACH
  - Same-currency EUR/SEPA → SEPA Credit Transfer + requires_iban
  - Same-currency non-SEPA foreign → international_wire (no FX,
    but still SWIFT rails)
  - Explicit `requested_method` override is honored AND requirement
    flags are still set so downstream validation doesn't skip
"""

from __future__ import annotations

from decimal import Decimal

from app.services.payment_corridor import pick_corridor

# ---------------------------------------------------------------------------
# Cross-currency → international wire + FX leg.
# ---------------------------------------------------------------------------


def test_cross_currency_routes_to_international_wire_with_fx():
    """USD home → EUR invoice → international_wire, requires_fx=True,
    requires_swift=True, processor hint nudges toward Wise (cheaper
    intl rails than Modern Treasury for non-USD)."""
    c = pick_corridor(
        source_currency="USD",
        target_currency="EUR",
        target_country="DE",
    )
    assert c.method == "international_wire"
    assert c.requires_fx is True
    assert c.requires_swift is True
    assert c.requires_iban is True  # DE is SEPA → IBAN expected
    assert c.processor_hint == "wise"


def test_cross_currency_to_non_sepa_country_does_not_require_iban():
    """USD → JPY to Japan → international wire, FX leg, SWIFT
    required, but no IBAN (Japan doesn't use IBAN)."""
    c = pick_corridor(
        source_currency="USD",
        target_currency="JPY",
        target_country="JP",
    )
    assert c.method == "international_wire"
    assert c.requires_fx is True
    assert c.requires_swift is True
    assert c.requires_iban is False


def test_cross_currency_with_no_country_still_picks_international_wire():
    """A missing target_country shouldn't crash — fall back to
    international_wire without IBAN demand."""
    c = pick_corridor(
        source_currency="USD",
        target_currency="EUR",
        target_country=None,
    )
    assert c.method == "international_wire"
    assert c.requires_fx is True


# ---------------------------------------------------------------------------
# Same-currency domestic (US) → ACH.
# ---------------------------------------------------------------------------


def test_us_domestic_routes_to_ach():
    """USD → USD to the US → ACH, no FX, no SWIFT, no IBAN."""
    c = pick_corridor(
        source_currency="USD",
        target_currency="USD",
        target_country="US",
    )
    assert c.method == "ach"
    assert c.requires_fx is False
    assert c.requires_swift is False
    assert c.requires_iban is False
    assert c.expected_fee_pct == Decimal("0.0010")


def test_us_domestic_with_no_country_defaults_to_us():
    """USD → USD with no country → still ACH. Most invoices upload
    without an address country; defaulting to US is the right
    fallback because the org's home currency is the destination
    cue."""
    c = pick_corridor(
        source_currency="USD",
        target_currency="USD",
        target_country=None,
    )
    assert c.method == "ach"


# ---------------------------------------------------------------------------
# Same-currency EUR within SEPA → SEPA Credit Transfer.
# ---------------------------------------------------------------------------


def test_eur_to_sepa_country_routes_to_sepa():
    """EUR → EUR to a SEPA country → SEPA. IBAN required, no
    SWIFT, no FX leg, fee under 0.001 (≈ free)."""
    c = pick_corridor(
        source_currency="EUR",
        target_currency="EUR",
        target_country="DE",
    )
    assert c.method == "sepa"
    assert c.requires_iban is True
    assert c.requires_swift is False
    assert c.requires_fx is False
    assert c.expected_fee_pct < Decimal("0.001")
    assert c.processor_hint == "wise"


def test_eur_to_non_sepa_country_routes_to_international_wire():
    """EUR → EUR to a non-SEPA country (say Brazil) → international
    wire, SWIFT required, no IBAN, no FX leg (same currency)."""
    c = pick_corridor(
        source_currency="EUR",
        target_currency="EUR",
        target_country="BR",
    )
    assert c.method == "international_wire"
    assert c.requires_swift is True
    assert c.requires_iban is False
    assert c.requires_fx is False


# ---------------------------------------------------------------------------
# Same-currency, foreign, same-rails (non-USD non-SEPA).
# ---------------------------------------------------------------------------


def test_jpy_to_jp_routes_to_international_wire_no_fx():
    """JPY home → JPY to Japan → still international wire (foreign
    rails), but no FX leg."""
    c = pick_corridor(
        source_currency="JPY",
        target_currency="JPY",
        target_country="JP",
    )
    assert c.method == "international_wire"
    assert c.requires_fx is False
    assert c.requires_swift is True


# ---------------------------------------------------------------------------
# Explicit method override.
# ---------------------------------------------------------------------------


def test_requested_method_override_is_honored():
    """Caller passes `requested_method="wire"` for a payment that
    would otherwise pick ACH. The override wins; requirement flags
    still set from the corridor's shape."""
    c = pick_corridor(
        source_currency="USD",
        target_currency="USD",
        target_country="US",
        requested_method="wire",
    )
    assert c.method == "wire"
    assert c.requires_fx is False
    # Wire still implies SWIFT routing fields downstream.
    assert c.requires_swift is True


def test_requested_method_override_does_not_strip_requires_fx():
    """Cross-currency override → method changes but requires_fx
    stays True. Otherwise the orchestration layer skips the FX
    lookup and the payment goes out with no rate locked → audit
    replay broken."""
    c = pick_corridor(
        source_currency="USD",
        target_currency="EUR",
        target_country="DE",
        requested_method="sepa",  # not a valid USD→EUR corridor but caller forced it
    )
    assert c.method == "sepa"
    assert c.requires_fx is True


def test_requested_method_virtual_card_does_not_require_swift():
    """Virtual card overrides shouldn't pull in SWIFT/IBAN
    requirements — they go via the card network."""
    c = pick_corridor(
        source_currency="USD",
        target_currency="USD",
        target_country="US",
        requested_method="virtual_card",
    )
    assert c.method == "virtual_card"
    assert c.requires_swift is False


# ---------------------------------------------------------------------------
# CorridorChoice shape.
# ---------------------------------------------------------------------------


def test_corridor_choice_is_immutable():
    """Frozen dataclass — once selected, the corridor for a payment
    can't be edited mid-flight by another code path."""
    c = pick_corridor(source_currency="USD", target_currency="USD", target_country="US")
    import dataclasses

    assert dataclasses.is_dataclass(c)
    # Frozen → setting an attribute raises.
    import pytest

    with pytest.raises(dataclasses.FrozenInstanceError):
        c.method = "wire"  # type: ignore[misc]


def test_corridor_notes_explain_the_choice():
    """Every choice carries a one-line `notes` field describing why
    it was picked. The AP team reads these in the payment-detail
    panel."""
    c1 = pick_corridor(source_currency="USD", target_currency="EUR", target_country="DE")
    assert "cross-currency" in c1.notes.lower()
    c2 = pick_corridor(source_currency="USD", target_currency="USD", target_country="US")
    assert "domestic" in c2.notes.lower()
    c3 = pick_corridor(source_currency="EUR", target_currency="EUR", target_country="FR")
    assert "sepa" in c3.notes.lower()
