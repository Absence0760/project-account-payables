"""Payment corridor selection — pick a payment method for a given
(source currency, target currency, target country) tuple.

A "corridor" is the path money takes from the originating account
to the vendor. The right corridor depends on the destination:

  - USD → USD in the US           → ACH (fast + cheap) or wire (urgent)
  - EUR → EUR within SEPA zone    → SEPA Credit Transfer (free + 1-day)
  - Same currency to same country → existing domestic ACH path
  - Cross-currency (any)          → international SWIFT wire
                                    (FX leg priced separately)
  - Cross-currency to SEPA        → SWIFT wire with FX conversion
                                    (real prod would use Wise/Tipalti
                                    "borderless" rails for cost)

This file deliberately stays a pure function: no DB, no IO. Callers
(see `services/international_payments.py`) take a CorridorChoice and
do the rate lookup + Payment row construction themselves.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.utils.banking import is_sepa_country


@dataclass(frozen=True)
class CorridorChoice:
    """The chosen path for one payment.

    `method` matches the `Payment.method` column. New international
    values are `sepa` and `international_wire`; the existing US
    domestic values (`ach`, `wire`, `rtp`, `check`, `virtual_card`)
    are returned as-is for compatibility.

    `expected_fee_pct` is the fee we surface in the UI for the AP
    team. It's not the *actual* fee — that's processor-specific —
    just a "this corridor typically costs about X%" hint so the
    operator can choose between a cheaper-but-slower SEPA and a
    faster-but-pricier wire.

    `processor_hint` is the provider we'd prefer if more than one
    is enabled (e.g. `wise` for SEPA, `modern_treasury` for
    domestic). Empty when the org's default is fine.
    """

    method: str
    expected_fee_pct: Decimal
    processor_hint: str = ""
    requires_fx: bool = False
    requires_swift: bool = False
    requires_iban: bool = False
    notes: str = ""


# Fee anchors — order-of-magnitude estimates, not contracts. Used to
# render a "≈ $X" badge on the payment screen so the AP team can pick.
# Real fees come from the processor on settlement.
_FEE_ACH = Decimal("0.0010")       # 0.1 %
_FEE_DOMESTIC_WIRE = Decimal("0.0050")
_FEE_SEPA = Decimal("0.0005")      # ~free in practice
_FEE_INTL_WIRE = Decimal("0.0250") # SWIFT correspondent banks add up
_FEE_RTP = Decimal("0.0020")


def pick_corridor(
    *,
    source_currency: str,
    target_currency: str,
    target_country: str | None,
    requested_method: str | None = None,
) -> CorridorChoice:
    """Return the right CorridorChoice for the given destination.

    Resolution order:
      1. If caller explicitly asked for a method (UI override or
         vendor preference), honor it — set requires_fx /
         requires_swift / requires_iban flags from the corridor's
         shape so validation downstream can still refuse the payment
         if the vendor's bank row is incomplete.
      2. Same currency, US destination → ACH.
      3. Same currency, SEPA destination → SEPA Credit Transfer.
      4. Same currency, anywhere else → international wire (SWIFT).
      5. Different currency → international wire with FX leg.
    """
    src = source_currency.upper()
    tgt = target_currency.upper()
    country = (target_country or "").upper() or None
    requires_fx = src != tgt

    if requested_method:
        # Caller is overriding — derive only the requirement flags
        # from the corridor's shape. Fee is a best-effort lookup.
        method = requested_method.lower()
        fee = {
            "ach": _FEE_ACH,
            "wire": _FEE_DOMESTIC_WIRE if not requires_fx else _FEE_INTL_WIRE,
            "rtp": _FEE_RTP,
            "sepa": _FEE_SEPA,
            "international_wire": _FEE_INTL_WIRE,
            "check": Decimal("0"),
            "virtual_card": Decimal("0"),
        }.get(method, _FEE_INTL_WIRE)
        return CorridorChoice(
            method=method,
            expected_fee_pct=fee,
            requires_fx=requires_fx,
            requires_swift=method in {"wire", "international_wire"} or requires_fx,
            requires_iban=method == "sepa" or is_sepa_country(country),
            notes="explicit method override",
        )

    # Cross-currency always goes via SWIFT wire — the FX leg is
    # priced in the orchestration layer, the rails are still wire.
    if requires_fx:
        return CorridorChoice(
            method="international_wire",
            expected_fee_pct=_FEE_INTL_WIRE,
            processor_hint="wise",
            requires_fx=True,
            requires_swift=True,
            requires_iban=is_sepa_country(country),
            notes=f"cross-currency {src}→{tgt}",
        )

    # Same-currency paths.
    if tgt == "USD" and (country is None or country == "US"):
        return CorridorChoice(
            method="ach",
            expected_fee_pct=_FEE_ACH,
            notes="US domestic ACH",
        )

    if tgt == "EUR" and is_sepa_country(country):
        return CorridorChoice(
            method="sepa",
            expected_fee_pct=_FEE_SEPA,
            processor_hint="wise",
            requires_iban=True,
            notes="SEPA Credit Transfer (EU same-currency)",
        )

    # Same currency, foreign country, not SEPA → SWIFT wire even
    # without FX. This is the GBP→GBP-to-UK path (no longer SEPA
    # post-Brexit but our SEPA list still includes GB for historical
    # compat — see banking.SEPA_COUNTRIES) and the JPY→Japan path.
    return CorridorChoice(
        method="international_wire",
        expected_fee_pct=_FEE_INTL_WIRE,
        processor_hint="wise",
        requires_fx=False,
        requires_swift=True,
        requires_iban=is_sepa_country(country),
        notes=f"foreign same-currency {tgt} to {country}",
    )
