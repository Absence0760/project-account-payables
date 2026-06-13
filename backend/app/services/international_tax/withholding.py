"""Withholding-tax computation by jurisdiction + payment category.

Withholding tax (WHT) is deducted by the *payer* from a payment to a
foreign (or sometimes domestic) supplier and remitted to the tax
authority. The rate depends on the supplier's country, the *category* of
payment (services / royalties / interest / dividends / contractor / rent),
and supplier-specific facts (e.g. an Australian supplier that fails to
quote an ABN is subject to 47% no-ABN withholding).

Rates live in the country-rules engine (``CountryTaxRule.withholding``);
this layer just selects the matching bracket and does the Decimal math.
``net_payable`` is what the supplier actually receives after the deduction.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from app.services.international_tax.country_rules import (
    WithholdingRule,
    get_country_rule,
)

_CENTS = Decimal("0.01")


def _round_money(value: Decimal) -> Decimal:
    return value.quantize(_CENTS, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class WithholdingComputation:
    """Result of a withholding computation on one payment.

    - ``gross_amount`` — payment before withholding.
    - ``withholding_rate`` — percent applied.
    - ``withholding_amount`` — tax withheld and remitted to the authority.
    - ``net_payable`` — what the supplier receives (gross − withheld).
    - ``treaty_applied`` — True when a reduced double-tax-treaty rate was
      supplied by the caller and used instead of the statutory rate.
    """

    country_code: str
    currency: str
    category: str
    gross_amount: Decimal
    withholding_rate: Decimal
    withholding_amount: Decimal
    net_payable: Decimal
    treaty_applied: bool
    notes: str


def _select_rule(rules: tuple[WithholdingRule, ...], category: str | None) -> WithholdingRule:
    """Pick the bracket matching ``category``, else the default bracket,
    else a synthesized zero-rate bracket (country has no WHT in scope)."""
    if category:
        cat = category.strip().lower()
        for r in rules:
            if r.category.lower() == cat:
                return r
    for r in rules:
        if r.default:
            return r
    return WithholdingRule(category=category or "default", rate=Decimal("0"), default=True)


def compute_withholding(
    *,
    gross_amount: Decimal,
    supplier_country: str,
    category: str | None = None,
    treaty_rate: Decimal | None = None,
    currency: str = "",
) -> WithholdingComputation:
    """Compute withholding tax on a payment to a supplier.

    ``category`` selects the WHT bracket (services / royalties / interest /
    dividends / contractor / rent / no_abn / professional_services / ...).
    ``treaty_rate`` overrides the statutory rate with a reduced double-tax-
    treaty rate when the supplier has provided treaty residency proof — only
    used when it is *lower* than the statutory rate (a treaty can reduce, not
    raise, the rate).
    """
    gross = Decimal(gross_amount)
    rule = get_country_rule(supplier_country)
    bracket = _select_rule(rule.withholding, category)

    statutory = bracket.rate
    treaty_applied = False
    rate = statutory
    if treaty_rate is not None:
        tr = Decimal(treaty_rate)
        if tr < statutory:
            rate = tr
            treaty_applied = True

    withheld = _round_money(gross * rate / Decimal("100"))
    net_payable = _round_money(gross - withheld)

    if treaty_applied:
        notes = f"Treaty rate {rate}% applied (statutory {statutory}%)."
    elif rate == 0:
        notes = "No withholding tax applies to this category."
    else:
        notes = f"Statutory withholding at {rate}% on {bracket.category}."

    return WithholdingComputation(
        country_code=rule.country_code,
        currency=currency or rule.currency,
        category=bracket.category,
        gross_amount=_round_money(gross),
        withholding_rate=rate,
        withholding_amount=withheld,
        net_payable=net_payable,
        treaty_applied=treaty_applied,
        notes=notes,
    )
