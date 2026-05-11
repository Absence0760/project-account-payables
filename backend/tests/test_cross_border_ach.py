"""Cross-border ACH corridor — NACHA Global ACH (IAT) to a small set
of supported countries.

`pick_corridor` previously sent USD-to-foreign payments through
international_wire regardless of destination, which is wrong: SWIFT
wires cost ~2.5% while NACHA Global ACH to CA / MX / GB / select
LATAM corridors clears at ~$5–8 flat (≈ 0.8% on the test amounts).

Tests pin:
  - USD → USD to CA, MX, GB, BR routes to `international_ach`
  - The corridor doesn't demand IBAN or SWIFT (uses local account
    formats), and doesn't trigger an FX leg
  - Explicit override to `international_ach` is honored with the
    right fee anchor
  - USD → USD to JP (not on the Global ACH list) still falls
    through to international_wire — pin the negative case
  - is_international_payment recognises international_ach as
    international even without an fx_rate
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from app.services.international_payments import is_international_payment
from app.services.payment_corridor import pick_corridor


def test_usd_to_canada_routes_to_international_ach():
    c = pick_corridor(source_currency="USD", target_currency="USD", target_country="CA")
    assert c.method == "international_ach"
    assert c.requires_fx is False
    assert c.requires_swift is False
    assert c.requires_iban is False
    assert c.processor_hint == "modern_treasury"
    assert c.expected_fee_pct == Decimal("0.0080")


def test_usd_to_mexico_routes_to_international_ach():
    c = pick_corridor(source_currency="USD", target_currency="USD", target_country="MX")
    assert c.method == "international_ach"
    assert c.requires_swift is False


def test_usd_to_gb_routes_to_international_ach_despite_sepa_membership():
    """GB is in SEPA_COUNTRIES (historical compat) but it's also a
    Global-ACH destination. The Global-ACH branch comes after the
    SEPA branch in the resolver, but SEPA only fires on EUR — so a
    USD-to-GB payment should hit Global ACH, not SEPA."""
    c = pick_corridor(source_currency="USD", target_currency="USD", target_country="GB")
    assert c.method == "international_ach"


def test_usd_to_brazil_routes_to_international_ach():
    """LATAM is the second-biggest demand for Global ACH after CA/MX."""
    c = pick_corridor(source_currency="USD", target_currency="USD", target_country="BR")
    assert c.method == "international_ach"
    assert c.processor_hint == "modern_treasury"


def test_usd_to_japan_falls_through_to_international_wire():
    """JP is NOT a Global-ACH destination → fall through to SWIFT
    wire. Pin the negative case so a regression that loosened the
    country list doesn't silently re-route Japan payments."""
    c = pick_corridor(source_currency="USD", target_currency="USD", target_country="JP")
    assert c.method == "international_wire"


def test_eur_invoice_to_canada_still_uses_intl_wire_with_fx():
    """Cross-currency wins over the Global-ACH gate — paying a
    Canadian vendor in EUR needs the FX leg, so it goes wire."""
    c = pick_corridor(source_currency="USD", target_currency="EUR", target_country="CA")
    assert c.method == "international_wire"
    assert c.requires_fx is True


def test_explicit_international_ach_override_carries_correct_fee():
    """Caller forces `international_ach` for a domestic USD/USD
    invoice. Method stays, requirement flags relaxed (no IBAN
    needed for IAT)."""
    c = pick_corridor(
        source_currency="USD",
        target_currency="USD",
        target_country="US",
        requested_method="international_ach",
    )
    assert c.method == "international_ach"
    assert c.requires_iban is False
    assert c.expected_fee_pct == Decimal("0.0080")


def test_is_international_payment_recognises_intl_ach():
    """A payment with corridor=international_ach but no fx_rate is
    still international (foreign rails) — the predicate must say
    True so reporting and audit trails treat it correctly."""
    p = SimpleNamespace(fx_rate=None, corridor="international_ach")
    assert is_international_payment(p) is True
