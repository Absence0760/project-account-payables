"""GST computation — Australia, India, Canada (and other GST jurisdictions).

GST mechanics differ by country, so this layer reads the country-rules
engine and applies the right split:

- **Australia (AU)** — single 10% GST.
- **India (IN)** — dual GST. Intra-state supplies split the rate into
  equal CGST + SGST halves; inter-state supplies levy a single IGST at the
  full rate. ``interstate=True`` selects IGST.
- **Canada (CA)** — federal GST (5%) plus an optional provincial component
  (PST, or HST which combines both). The caller passes the province rate
  (percent) when applicable; we keep the federal and provincial figures
  separate so a tax report can break them out.

All amounts are ``Decimal``; tax rounds to 2 places ``ROUND_HALF_UP``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from app.services.international_tax.country_rules import (
    TaxRegime,
    get_country_rule,
)

_CENTS = Decimal("0.01")


def _round_money(value: Decimal) -> Decimal:
    return value.quantize(_CENTS, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class GSTComputation:
    """Result of a GST computation.

    ``components`` breaks the total tax into named parts (e.g. ``cgst`` +
    ``sgst`` for India, ``gst`` + ``pst`` for Canada) so a tax report can
    reconcile each line. Every component value is a ``Decimal``.
    """

    country_code: str
    currency: str
    net_amount: Decimal
    gst_rate: Decimal
    gst_amount: Decimal
    gross_amount: Decimal
    components: dict[str, Decimal] = field(default_factory=dict)
    notes: str = ""


def compute_gst(
    *,
    net_amount: Decimal,
    rate: Decimal,
    country: str,
    interstate: bool = False,
    province_rate: Decimal | None = None,
) -> GSTComputation:
    """Compute GST on a supply for a GST jurisdiction.

    ``rate`` is the percent resolved by the tax-rate adapter for the
    country. ``interstate`` selects IGST vs CGST/SGST for India.
    ``province_rate`` adds a Canadian provincial component on top of federal
    GST. Raises if the country isn't a GST jurisdiction.
    """
    net = Decimal(net_amount)
    rate = Decimal(rate)
    rule = get_country_rule(country)
    if rule.regime != TaxRegime.GST:
        raise ValueError(
            f"{rule.country_code} is not a GST jurisdiction (regime={rule.regime}); "
            "use the VAT path."
        )

    code = rule.country_code
    if code == "IN":
        return _compute_india(net, rate, rule.currency, interstate)
    if code == "CA":
        return _compute_canada(net, rate, rule.currency, province_rate)
    return _compute_single(net, rate, code, rule.currency)


def _compute_single(net: Decimal, rate: Decimal, code: str, currency: str) -> GSTComputation:
    gst = _round_money(net * rate / Decimal("100"))
    return GSTComputation(
        country_code=code,
        currency=currency,
        net_amount=_round_money(net),
        gst_rate=rate,
        gst_amount=gst,
        gross_amount=_round_money(net + gst),
        components={"gst": gst},
        notes=f"Single GST at {rate}%.",
    )


def _compute_india(net: Decimal, rate: Decimal, currency: str, interstate: bool) -> GSTComputation:
    total = _round_money(net * rate / Decimal("100"))
    if interstate:
        components = {"igst": total}
        notes = f"Inter-state supply: IGST at {rate}%."
    else:
        # CGST + SGST each take half the rate. Compute one half then derive
        # the other as the remainder so the two always sum to `total` even
        # when the half-cent rounds.
        cgst = _round_money(net * (rate / Decimal("2")) / Decimal("100"))
        sgst = _round_money(total - cgst)
        components = {"cgst": cgst, "sgst": sgst}
        notes = f"Intra-state supply: CGST + SGST, {rate}% combined."
    return GSTComputation(
        country_code="IN",
        currency=currency,
        net_amount=_round_money(net),
        gst_rate=rate,
        gst_amount=total,
        gross_amount=_round_money(net + total),
        components=components,
        notes=notes,
    )


def _compute_canada(
    net: Decimal, federal_rate: Decimal, currency: str, province_rate: Decimal | None
) -> GSTComputation:
    gst = _round_money(net * federal_rate / Decimal("100"))
    components: dict[str, Decimal] = {"gst": gst}
    total = gst
    notes = f"Federal GST at {federal_rate}%."
    if province_rate is not None:
        prov_rate = Decimal(province_rate)
        pst = _round_money(net * prov_rate / Decimal("100"))
        components["pst"] = pst
        total = _round_money(total + pst)
        notes = f"Federal GST {federal_rate}% + provincial {prov_rate}%."
    return GSTComputation(
        country_code="CA",
        currency=currency,
        net_amount=_round_money(net),
        gst_rate=federal_rate,
        gst_amount=total,
        gross_amount=_round_money(net + total),
        components=components,
        notes=notes,
    )
