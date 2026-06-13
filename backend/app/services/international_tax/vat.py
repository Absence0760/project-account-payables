"""VAT computation for international invoices, incl. EU reverse charge.

All amounts are ``Decimal`` and tax is rounded to 2 places with
``ROUND_HALF_UP`` (the standard VAT rounding direction). Nothing here ever
touches ``float`` — the *money is exact* invariant applies to tax math.

Reverse charge (the EU B2B mechanism): when a VAT-registered business in
one EU member state buys from a supplier in another EU member state, the
supplier invoices *without* VAT and the buyer self-accounts for it. From
the AP side this means: the buyer owes no cash VAT to the supplier (the
payable VAT is netted by an equal input credit), but the transaction must
still be *reported*. We model that as ``reverse_charge=True`` with a
``reportable_vat`` figure and ``vat_payable=0``.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from app.services.international_tax.country_rules import (
    TaxRegime,
    get_country_rule,
    is_eu_country,
)

_CENTS = Decimal("0.01")


def _round_money(value: Decimal) -> Decimal:
    return value.quantize(_CENTS, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class VATComputation:
    """Result of a VAT computation on one invoice/supply.

    - ``net_amount`` — taxable base (ex-VAT).
    - ``vat_rate`` — percent applied (Decimal).
    - ``vat_amount`` — VAT on the supply at that rate.
    - ``vat_payable`` — cash VAT actually owed to the supplier (0 under
      reverse charge).
    - ``reportable_vat`` — VAT to declare on the return (equals
      ``vat_amount`` under reverse charge; the buyer self-accounts).
    - ``gross_amount`` — net + cash VAT payable.
    - ``reverse_charge`` — True when the EU B2B mechanism applied.
    """

    country_code: str
    currency: str
    net_amount: Decimal
    vat_rate: Decimal
    vat_amount: Decimal
    vat_payable: Decimal
    reportable_vat: Decimal
    gross_amount: Decimal
    reverse_charge: bool
    notes: str


def compute_vat(
    *,
    net_amount: Decimal,
    rate: Decimal,
    supplier_country: str,
    buyer_country: str | None = None,
    buyer_vat_registered: bool = False,
    currency: str = "",
) -> VATComputation:
    """Compute VAT on a supply.

    ``rate`` is the percent resolved by the tax-rate adapter for the
    *supplier's* jurisdiction. Reverse charge applies when supplier and
    buyer are *different* EU member states and the buyer is VAT-registered;
    in that case the supplier charges no VAT and the buyer self-accounts.

    Raises if the supplier country's regime isn't VAT — callers should route
    GST jurisdictions through ``gst.compute_gst`` instead.
    """
    net = Decimal(net_amount)
    rate = Decimal(rate)
    rule = get_country_rule(supplier_country)
    if rule.regime != TaxRegime.VAT:
        raise ValueError(
            f"{rule.country_code} is not a VAT jurisdiction (regime={rule.regime}); "
            "use the GST path."
        )

    vat_amount = _round_money(net * rate / Decimal("100"))

    reverse_charge = _is_reverse_charge(
        supplier_country=rule.country_code,
        buyer_country=buyer_country,
        buyer_vat_registered=buyer_vat_registered,
        rule_supports_rc=rule.reverse_charge_supported,
    )

    if reverse_charge:
        # Supplier invoices without VAT; buyer self-accounts. No cash VAT,
        # but the VAT is still reportable on the buyer's return.
        vat_payable = Decimal("0.00")
        reportable_vat = vat_amount
        gross = _round_money(net)
        notes = (
            "EU reverse charge: supplier charges no VAT; buyer self-accounts "
            "the VAT on its return (net input/output)."
        )
    else:
        vat_payable = vat_amount
        reportable_vat = vat_amount
        gross = _round_money(net + vat_amount)
        notes = "Standard VAT charged by the supplier."

    return VATComputation(
        country_code=rule.country_code,
        currency=currency or rule.currency,
        net_amount=_round_money(net),
        vat_rate=rate,
        vat_amount=vat_amount,
        vat_payable=vat_payable,
        reportable_vat=reportable_vat,
        gross_amount=gross,
        reverse_charge=reverse_charge,
        notes=notes,
    )


def _is_reverse_charge(
    *,
    supplier_country: str,
    buyer_country: str | None,
    buyer_vat_registered: bool,
    rule_supports_rc: bool,
) -> bool:
    """EU intra-community B2B reverse charge test.

    Conditions: both supplier and buyer are EU member states, they differ,
    the buyer is VAT-registered, and the supplier's rule supports RC. A
    purely domestic supply (same country) is never reverse charge.
    """
    if not buyer_country or not buyer_vat_registered or not rule_supports_rc:
        return False
    sup = supplier_country.strip().upper()
    buy = buyer_country.strip().upper()
    if sup == buy:
        return False
    return is_eu_country(sup) and is_eu_country(buy)


def validate_vat_number(country_code: str, vat_number: str | None) -> bool:
    """Lightweight structural VAT-number check.

    NOT a VIES lookup — just a presence + country-prefix + length sanity
    check so an obviously malformed number is caught before it reaches an
    ERP. Returns True when the number is plausibly well-formed for the
    country. The VAT number itself is treated as low-sensitivity business
    metadata, but we never log it here regardless.
    """
    if not vat_number:
        return False
    rule = get_country_rule(country_code)
    cleaned = vat_number.replace(" ", "").upper()
    # EU numbers are prefixed with the 2-letter country code; many non-EU
    # VAT countries are not. Require a minimum body length either way.
    if rule.is_eu:
        if not cleaned.startswith(rule.country_code):
            return False
        body = cleaned[len(rule.country_code) :]
    else:
        body = cleaned
    return len(body) >= 5 and any(ch.isdigit() for ch in body)
