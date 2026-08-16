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

from app.services.payment_methods import is_international_payment_method
from app.utils.banking import is_sepa_country

# NACHA Global ACH supports USD-originated cross-border ACH to a small
# set of countries via correspondent banks (typically a 2–3 day SLA at
# ~$5 flat per transaction — cheaper than SWIFT for low-value
# recurring payments). Anything outside this list falls back to
# SWIFT wire when the destination is foreign-USD. Source: NACHA's
# International ACH Transaction (IAT) corridor list.
_GLOBAL_ACH_DESTINATIONS: frozenset[str] = frozenset(
    {
        "CA",  # Canada
        "MX",  # Mexico
        "GB",  # United Kingdom (post-Brexit GBP corridor; USD payment
        # arrives via correspondent → local clearing)
        "PA",  # Panama (USD-anchored)
        "AR",  # Argentina (USD wires re-routed via IAT)
        "BR",  # Brazil
        "CL",  # Chile
        "CO",  # Colombia
        "DO",  # Dominican Republic
        "PE",  # Peru
        "VE",  # Venezuela (sanctions caveats apply — see KYC/AML layer)
    }
)


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


# Methods that ONLY ever arrive as an explicit choice — nothing defaults to
# them, so seeing one really does mean the caller (AP user or vendor
# preference) asked for it. Contrast the plain domestic/generic methods
# (ach/wire/rtp/check/virtual_card), which `create_payment_run` defaults
# every line item to regardless of the invoice's actual currency/country —
# a truthy `requested_method` of one of those can NOT be trusted as "the user
# explicitly chose this" the way an explicit international method can.
#
# That set is `payment_methods.INTERNATIONAL_PAYMENT_METHODS`, imported rather
# than restated: `compliance` (KYC thresholds) and `api/payments` (whether an
# FX rate is locked) ask the same question, and three private copies meant a
# fourth international rail had to be remembered in three places.

# Fee anchors — order-of-magnitude estimates, not contracts. Used to
# render a "≈ $X" badge on the payment screen so the AP team can pick.
# Real fees come from the processor on settlement.
_FEE_ACH = Decimal("0.0010")  # 0.1 %
_FEE_DOMESTIC_WIRE = Decimal("0.0050")
_FEE_SEPA = Decimal("0.0005")  # ~free in practice
_FEE_INTL_WIRE = Decimal("0.0250")  # SWIFT correspondent banks add up
_FEE_RTP = Decimal("0.0020")
_FEE_INTL_ACH = Decimal("0.0080")  # NACHA Global ACH — between SEPA and wire

# Every `Payment.method` value an explicit caller override can select, mapped
# to its fee anchor. Public because it is the authoritative list of rails this
# selector can stamp onto a Payment row — `services/payment_methods` classifies
# each of them for IRS 1099 reporting and its drift guard reads these keys, so
# a new rail added here can't silently ship without a tax treatment.
# `wire` is listed at its domestic anchor; `pick_corridor` re-prices it at the
# international anchor when the corridor needs an FX leg.
CORRIDOR_OVERRIDE_FEES: dict[str, Decimal] = {
    "ach": _FEE_ACH,
    "wire": _FEE_DOMESTIC_WIRE,
    "rtp": _FEE_RTP,
    "sepa": _FEE_SEPA,
    "international_ach": _FEE_INTL_ACH,
    "international_wire": _FEE_INTL_WIRE,
    "check": Decimal("0"),
    "virtual_card": Decimal("0"),
}


def pick_corridor(
    *,
    source_currency: str,
    target_currency: str,
    target_country: str | None,
    requested_method: str | None = None,
) -> CorridorChoice:
    """Return the right CorridorChoice for the given destination.

    Resolution order:
      1. If caller explicitly asked for a method AND it's trustworthy as a
         real choice — an explicit international method (sepa /
         international_wire / international_ach), or ANY method for a
         genuinely domestic destination — honor it: set requires_fx /
         requires_swift / requires_iban flags from the corridor's shape so
         validation downstream can still refuse the payment if the vendor's
         bank row is incomplete. A plain domestic-looking method
         (ach/wire/rtp/check) for a destination that actually needs
         international routing is `create_payment_run`'s blanket default,
         not a real choice, and falls through to auto-selection instead.
      2. Same currency, US destination → ACH.
      3. Same currency, SEPA destination → SEPA Credit Transfer.
      4. Same currency, anywhere else → international wire (SWIFT).
      5. Different currency → international wire with FX leg.
    """
    src = source_currency.upper()
    tgt = target_currency.upper()
    country = (target_country or "").upper() or None
    requires_fx = src != tgt
    is_domestic_us = not requires_fx and tgt == "USD" and (country is None or country == "US")

    # Only honor `requested_method` as a real override when it's either an
    # EXPLICIT international method (nothing defaults to those) or the
    # destination is genuinely domestic (so a plain "ach"/"wire" default is
    # correct anyway). A truthy domestic-looking method for a destination
    # that actually needs international routing — cross-currency, or a
    # foreign country even at the same currency — is `create_payment_run`'s
    # blanket "ach" default, NOT a real choice; honoring it as one used to
    # send a domestic rail + foreign currency to the processor and fail
    # there instead of routing correctly. Fall through to auto-selection
    # below exactly as if no method had been requested.
    honor_override = bool(requested_method) and (
        is_international_payment_method(requested_method) or is_domestic_us
    )

    if honor_override:
        # Caller is overriding — derive only the requirement flags
        # from the corridor's shape. Fee is a best-effort lookup.
        method = requested_method.lower()
        fee = CORRIDOR_OVERRIDE_FEES.get(method, _FEE_INTL_WIRE)
        if method == "wire" and requires_fx:
            fee = _FEE_INTL_WIRE
        return CorridorChoice(
            method=method,
            expected_fee_pct=fee,
            requires_fx=requires_fx,
            requires_swift=method in {"wire", "international_wire"} or requires_fx,
            # international_ach uses local account formats, not IBAN,
            # but Canadian / European destinations may still carry one
            # — we leave it to the orchestrator to enforce on the
            # SEPA-country case if the caller forces it.
            requires_iban=method == "sepa"
            or (method != "international_ach" and is_sepa_country(country)),
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

    # USD outbound to a Global-ACH destination → IAT (international
    # ACH). Cheaper than SWIFT for low-value payments to CA/MX/UK +
    # selected LATAM corridors. The funding leg is USD; the receiving
    # bank handles its own end-of-day conversion if the beneficiary
    # account is local-currency. We treat this as "no FX" from our
    # ledger's perspective — the rate at the receiving end isn't ours
    # to lock.
    if tgt == "USD" and country in _GLOBAL_ACH_DESTINATIONS:
        return CorridorChoice(
            method="international_ach",
            expected_fee_pct=_FEE_INTL_ACH,
            processor_hint="modern_treasury",
            requires_fx=False,
            requires_swift=False,
            requires_iban=False,
            notes=f"NACHA Global ACH (IAT) to {country}",
        )

    # Same currency, foreign country, not SEPA, not a Global-ACH
    # destination → SWIFT wire. This is the GBP→GBP-to-UK path and
    # the JPY→Japan path.
    return CorridorChoice(
        method="international_wire",
        expected_fee_pct=_FEE_INTL_WIRE,
        processor_hint="wise",
        requires_fx=False,
        requires_swift=True,
        requires_iban=is_sepa_country(country),
        notes=f"foreign same-currency {tgt} to {country}",
    )
