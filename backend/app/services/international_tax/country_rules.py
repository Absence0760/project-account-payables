"""Country-specific tax rules engine — data-driven, no code per country.

Every behavioural difference between jurisdictions (which tax *regime*
applies, the standard rate, whether reverse-charge / GST / withholding
exist, the registration-id label) is expressed as a row in
``COUNTRY_RULES`` keyed by ISO 3166-1 alpha-2 country code. Adding a new
country is a data edit here, not a code change in the VAT / GST /
withholding services — those services read this table.

This keeps the "Country-specific tax rules engine" requirement honest:
the rules are config, and the computation layers (``vat.py``,
``gst.py``, ``withholding.py``) are generic over them.

All monetary rates are ``Decimal`` (percent, e.g. ``Decimal("20")`` for
20%), never ``float`` — the *money is exact* project invariant covers
tax math the same as payment math.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


class TaxRegime:
    """Which consumption-tax family a country uses.

    Plain string constants (not an Enum) so a rules row reads as data and
    a new regime is a one-line addition.
    """

    VAT = "vat"  # EU, UK, ZA, etc.
    GST = "gst"  # AU, IN, CA, NZ, SG
    SALES_TAX = "sales_tax"  # US (state-level; out of scope for compute here)
    NONE = "none"  # no national consumption tax


@dataclass(frozen=True)
class WithholdingRule:
    """A withholding-tax bracket for a payment category in a country.

    ``rate`` is a percent (``Decimal("10")`` = 10%). ``category`` matches
    the caller-supplied payment category (services / royalties / interest
    / dividends / goods); ``default`` marks the fallback rule used when no
    category matches.
    """

    category: str
    rate: Decimal
    default: bool = False


@dataclass(frozen=True)
class CountryTaxRule:
    """The full rules row for one country.

    Frozen + Decimal throughout so a rule can't be mutated at runtime and
    a rate can't silently become a float.
    """

    country_code: str
    country_name: str
    regime: str
    currency: str
    # Standard consumption-tax rate (VAT or GST), percent. Decimal("0")
    # when the regime is NONE / SALES_TAX.
    standard_rate: Decimal
    # Reduced / zero rates keyed by a caller-supplied rate category label
    # ("reduced", "zero", "food", ...). Standard rate is implied; this is
    # only the exceptions.
    rate_categories: dict[str, Decimal] = field(default_factory=dict)
    # EU member states support the B2B reverse-charge mechanism on
    # cross-border intra-EU supplies. Non-EU VAT countries set False.
    is_eu: bool = False
    reverse_charge_supported: bool = False
    # Label the jurisdiction uses for a business tax-registration id
    # (VAT number, GSTIN, ABN, BN). Display-only; never logged with a value.
    registration_label: str = "Tax ID"
    # Withholding-tax brackets. Empty list = country has no WHT in scope.
    withholding: tuple[WithholdingRule, ...] = ()


def _wht(*rules: tuple[str, str, bool]) -> tuple[WithholdingRule, ...]:
    return tuple(WithholdingRule(category=c, rate=Decimal(r), default=d) for (c, r, d) in rules)


# ---------------------------------------------------------------------------
# The rules table. Rates are illustrative defaults a tenant can override via
# the tax-rate adapter; the *structure* (regime, reverse-charge, WHT shape)
# is the jurisdiction fact this engine encodes.
# ---------------------------------------------------------------------------
COUNTRY_RULES: dict[str, CountryTaxRule] = {
    # --- EU VAT (reverse-charge supported) ---
    "DE": CountryTaxRule(
        country_code="DE",
        country_name="Germany",
        regime=TaxRegime.VAT,
        currency="EUR",
        standard_rate=Decimal("19"),
        rate_categories={"reduced": Decimal("7"), "zero": Decimal("0")},
        is_eu=True,
        reverse_charge_supported=True,
        registration_label="VAT number",
        withholding=_wht(("royalties", "15", False), ("services", "0", True)),
    ),
    "FR": CountryTaxRule(
        country_code="FR",
        country_name="France",
        regime=TaxRegime.VAT,
        currency="EUR",
        standard_rate=Decimal("20"),
        rate_categories={
            "reduced": Decimal("10"),
            "super_reduced": Decimal("5.5"),
            "zero": Decimal("0"),
        },
        is_eu=True,
        reverse_charge_supported=True,
        registration_label="VAT number",
    ),
    "IE": CountryTaxRule(
        country_code="IE",
        country_name="Ireland",
        regime=TaxRegime.VAT,
        currency="EUR",
        standard_rate=Decimal("23"),
        rate_categories={
            "reduced": Decimal("13.5"),
            "second_reduced": Decimal("9"),
            "zero": Decimal("0"),
        },
        is_eu=True,
        reverse_charge_supported=True,
        registration_label="VAT number",
    ),
    "NL": CountryTaxRule(
        country_code="NL",
        country_name="Netherlands",
        regime=TaxRegime.VAT,
        currency="EUR",
        standard_rate=Decimal("21"),
        rate_categories={"reduced": Decimal("9"), "zero": Decimal("0")},
        is_eu=True,
        reverse_charge_supported=True,
        registration_label="VAT number",
    ),
    # --- Non-EU VAT (no intra-EU reverse charge) ---
    "GB": CountryTaxRule(
        country_code="GB",
        country_name="United Kingdom",
        regime=TaxRegime.VAT,
        currency="GBP",
        standard_rate=Decimal("20"),
        rate_categories={"reduced": Decimal("5"), "zero": Decimal("0")},
        is_eu=False,
        # Post-Brexit GB still operates a domestic reverse charge (e.g.
        # construction services); flagged supported, EU-status False.
        reverse_charge_supported=True,
        registration_label="VAT number",
        withholding=_wht(("services", "20", False), ("default", "0", True)),
    ),
    "ZA": CountryTaxRule(
        country_code="ZA",
        country_name="South Africa",
        regime=TaxRegime.VAT,
        currency="ZAR",
        standard_rate=Decimal("15"),
        rate_categories={"zero": Decimal("0")},
        is_eu=False,
        reverse_charge_supported=False,
        registration_label="VAT registration number",
        withholding=_wht(
            ("royalties", "15", False),
            ("interest", "15", False),
            ("dividends", "20", False),
            ("services", "0", True),
        ),
    ),
    # --- GST countries ---
    "AU": CountryTaxRule(
        country_code="AU",
        country_name="Australia",
        regime=TaxRegime.GST,
        currency="AUD",
        standard_rate=Decimal("10"),
        rate_categories={"gst_free": Decimal("0")},
        registration_label="ABN",
        withholding=_wht(
            # No-ABN withholding: payer must withhold 47% if a supplier
            # doesn't quote an ABN. Modelled as a named bracket.
            ("no_abn", "47", False),
            ("royalties", "30", False),
            ("default", "0", True),
        ),
    ),
    "IN": CountryTaxRule(
        country_code="IN",
        country_name="India",
        regime=TaxRegime.GST,
        currency="INR",
        # India's GST is split CGST+SGST (intra-state) or IGST (inter-state);
        # 18 is the common combined standard slab. The gst.py layer splits it.
        standard_rate=Decimal("18"),
        rate_categories={
            "slab_5": Decimal("5"),
            "slab_12": Decimal("12"),
            "slab_28": Decimal("28"),
            "exempt": Decimal("0"),
        },
        registration_label="GSTIN",
        withholding=_wht(
            # TDS (tax deducted at source) — common business slabs.
            ("professional_services", "10", False),
            ("contractor", "2", False),
            ("rent", "10", False),
            ("default", "0", True),
        ),
    ),
    "CA": CountryTaxRule(
        country_code="CA",
        country_name="Canada",
        regime=TaxRegime.GST,
        currency="CAD",
        # Federal GST is 5%; provinces layer PST/HST on top. The gst.py
        # layer applies the federal rate and any provided province rate.
        standard_rate=Decimal("5"),
        rate_categories={"zero": Decimal("0")},
        registration_label="Business Number",
    ),
    "NZ": CountryTaxRule(
        country_code="NZ",
        country_name="New Zealand",
        regime=TaxRegime.GST,
        currency="NZD",
        standard_rate=Decimal("15"),
        rate_categories={"zero": Decimal("0")},
        registration_label="GST number",
    ),
    "SG": CountryTaxRule(
        country_code="SG",
        country_name="Singapore",
        regime=TaxRegime.GST,
        currency="SGD",
        standard_rate=Decimal("9"),
        rate_categories={"zero": Decimal("0")},
        registration_label="GST registration number",
    ),
    # --- Sales-tax / no national consumption tax ---
    "US": CountryTaxRule(
        country_code="US",
        country_name="United States",
        regime=TaxRegime.SALES_TAX,
        currency="USD",
        standard_rate=Decimal("0"),  # state/local; computed elsewhere
        registration_label="EIN",
        withholding=_wht(("default", "0", True)),
    ),
    "AE": CountryTaxRule(
        country_code="AE",
        country_name="United Arab Emirates",
        regime=TaxRegime.VAT,
        currency="AED",
        standard_rate=Decimal("5"),
        rate_categories={"zero": Decimal("0")},
        reverse_charge_supported=True,
        registration_label="TRN",
    ),
}


class UnknownCountry(ValueError):
    """Raised when a country code has no rules row — fail loud rather than
    silently taxing at zero, which would under-collect."""


def get_country_rule(country_code: str) -> CountryTaxRule:
    """Look up the rules row for an ISO 3166-1 alpha-2 code (case-insensitive).

    Raises ``UnknownCountry`` for unconfigured codes so callers can decide
    whether to reject the invoice or fall back — never silently returns a
    zero-rate rule.
    """
    code = (country_code or "").strip().upper()
    rule = COUNTRY_RULES.get(code)
    if rule is None:
        raise UnknownCountry(f"No tax rules configured for country: {code!r}")
    return rule


def supported_countries() -> list[str]:
    """Sorted list of configured country codes — for the report endpoint's
    discovery surface and the rules-engine self-test."""
    return sorted(COUNTRY_RULES)


def is_eu_country(country_code: str) -> bool:
    """True if the country is an EU member state (matters for intra-EU
    reverse charge). Unknown countries are treated as non-EU."""
    code = (country_code or "").strip().upper()
    rule = COUNTRY_RULES.get(code)
    return bool(rule and rule.is_eu)
