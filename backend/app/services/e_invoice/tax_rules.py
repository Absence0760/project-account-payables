"""Country-specific tax validation for e-invoices (VAT / GST / IVA).

A shared building block used in two places:

* :mod:`app.services.e_invoice.validate` calls :func:`validate_tax_document`
  as an additive guard on *inbound* parsed documents.
* the *outbound* authenticated export route calls it (via ``assert_valid``)
  as a pre-generation guard so an AP user can't emit a non-compliant invoice.

Three checks, all PII-free (a :class:`FieldError` names the *field path* and a
generic code — never the tax-id, rate, or amount value):

1. **Tax-ID format** per country — EU member-state VAT regexes + GB VAT, AU ABN,
   NZ/IN/CA GST, MX/ES/IT IVA. ES/IT are EU members so their VAT regex doubles
   as the IVA check.
2. **Tax-rate plausibility** per regime — a rate outside the country's known
   standard/reduced set (with a small tolerance band) is ``implausible``.
3. **Reverse-charge / zero-rate** — categories ``Z`` (zero-rated) and
   ``AE``/``E`` (reverse-charge / exempt) are plausible at ``0.00`` so a
   legitimate 0% line is never flagged.

Design choice: an **unknown / unsupported** country code is *skipped* (returns
``None``), never rejected — we can't validate a regime we don't model, and
inbound documents legitimately arrive from any country. Only a KNOWN country
with a MALFORMED id fails.
"""

from __future__ import annotations

import re
from decimal import Decimal

from app.services.e_invoice.model import EInvoiceDocument
from app.services.e_invoice.validate import FieldError

# ---------------------------------------------------------------------------
# Tax-ID format regexes, keyed by ISO-2 country code.
#
# EU VAT: each member state has its own structure. GB (post-Brexit) keeps the
# GB-prefixed VAT number. AU uses the 11-digit ABN. NZ/IN/CA GST and MX IVA
# (RFC) follow their national patterns. Patterns are anchored and case-folded
# to upper before matching.
# ---------------------------------------------------------------------------
_TAX_ID_PATTERNS: dict[str, re.Pattern[str]] = {
    # --- EU member states (VAT / IVA) ---
    "AT": re.compile(r"^ATU\d{8}$"),
    "BE": re.compile(r"^BE0\d{9}$"),
    "BG": re.compile(r"^BG\d{9,10}$"),
    "CY": re.compile(r"^CY\d{8}[A-Z]$"),
    "CZ": re.compile(r"^CZ\d{8,10}$"),
    "DE": re.compile(r"^DE\d{9}$"),
    "DK": re.compile(r"^DK\d{8}$"),
    "EE": re.compile(r"^EE\d{9}$"),
    "ES": re.compile(r"^ES[A-Z0-9]\d{7}[A-Z0-9]$"),  # IVA (NIF)
    "FI": re.compile(r"^FI\d{8}$"),
    "FR": re.compile(r"^FR[A-Z0-9]{2}\d{9}$"),
    "GR": re.compile(r"^EL\d{9}$"),  # Greece uses the EL prefix
    "HR": re.compile(r"^HR\d{11}$"),
    "HU": re.compile(r"^HU\d{8}$"),
    "IE": re.compile(r"^IE\d{7}[A-W][A-IW]?$"),
    "IT": re.compile(r"^IT\d{11}$"),  # IVA (Partita IVA)
    "LT": re.compile(r"^LT(\d{9}|\d{12})$"),
    "LU": re.compile(r"^LU\d{8}$"),
    "LV": re.compile(r"^LV\d{11}$"),
    "MT": re.compile(r"^MT\d{8}$"),
    "NL": re.compile(r"^NL\d{9}B\d{2}$"),
    "PL": re.compile(r"^PL\d{10}$"),
    "PT": re.compile(r"^PT\d{9}$"),
    "RO": re.compile(r"^RO\d{2,10}$"),
    "SE": re.compile(r"^SE\d{12}$"),
    "SI": re.compile(r"^SI\d{8}$"),
    "SK": re.compile(r"^SK\d{10}$"),
    # --- UK VAT (GB prefix) ---
    "GB": re.compile(r"^GB(\d{9}|\d{12}|GD\d{3}|HA\d{3})$"),
    # --- GST / ABN ---
    "AU": re.compile(r"^\d{11}$"),  # ABN — 11 digits
    "NZ": re.compile(r"^\d{8,9}$"),  # GST / IRD number
    "IN": re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z]\d[A-Z\d][A-Z\d]$"),  # GSTIN
    "CA": re.compile(r"^\d{9}(RT\d{4})?$"),  # Business Number / GST/HST
    # --- IVA (LATAM) ---
    "MX": re.compile(r"^[A-Z&Ñ]{3,4}\d{6}[A-Z0-9]{3}$"),  # RFC
}

# ---------------------------------------------------------------------------
# Plausible tax-rate sets per country (percent). A rate outside this set (with
# a small tolerance for rounding) is flagged ``implausible``. 0 is always
# plausible (zero-rated / reverse-charge / exempt supplies exist everywhere).
# These are the standard + common reduced rates; they're a sanity band, not a
# full rate schedule.
# ---------------------------------------------------------------------------
_TAX_RATES: dict[str, set[Decimal]] = {
    "AT": {Decimal("20"), Decimal("13"), Decimal("10")},
    "BE": {Decimal("21"), Decimal("12"), Decimal("6")},
    "BG": {Decimal("20"), Decimal("9")},
    "CY": {Decimal("19"), Decimal("9"), Decimal("5")},
    "CZ": {Decimal("21"), Decimal("15"), Decimal("12"), Decimal("10")},
    "DE": {Decimal("19"), Decimal("7")},
    "DK": {Decimal("25")},
    "EE": {Decimal("22"), Decimal("20"), Decimal("9")},
    "ES": {Decimal("21"), Decimal("10"), Decimal("4")},
    "FI": {Decimal("24"), Decimal("25.5"), Decimal("14"), Decimal("10")},
    "FR": {Decimal("20"), Decimal("10"), Decimal("5.5"), Decimal("2.1")},
    "GR": {Decimal("24"), Decimal("13"), Decimal("6")},
    "HR": {Decimal("25"), Decimal("13"), Decimal("5")},
    "HU": {Decimal("27"), Decimal("18"), Decimal("5")},
    "IE": {Decimal("23"), Decimal("13.5"), Decimal("9"), Decimal("4.8")},
    "IT": {Decimal("22"), Decimal("10"), Decimal("5"), Decimal("4")},
    "LT": {Decimal("21"), Decimal("9"), Decimal("5")},
    "LU": {Decimal("17"), Decimal("16"), Decimal("14"), Decimal("8"), Decimal("3")},
    "LV": {Decimal("21"), Decimal("12"), Decimal("5")},
    "MT": {Decimal("18"), Decimal("7"), Decimal("5")},
    "NL": {Decimal("21"), Decimal("9")},
    "PL": {Decimal("23"), Decimal("8"), Decimal("5")},
    "PT": {Decimal("23"), Decimal("13"), Decimal("6")},
    "RO": {Decimal("19"), Decimal("9"), Decimal("5")},
    "SE": {Decimal("25"), Decimal("12"), Decimal("6")},
    "SI": {Decimal("22"), Decimal("9.5"), Decimal("5")},
    "SK": {Decimal("20"), Decimal("10")},
    "GB": {Decimal("20"), Decimal("5")},
    "AU": {Decimal("10")},
    "NZ": {Decimal("15")},
    "IN": {
        Decimal("28"),
        Decimal("18"),
        Decimal("12"),
        Decimal("5"),
        Decimal("3"),
        Decimal("0.25"),
    },
    "CA": {Decimal("5"), Decimal("13"), Decimal("15"), Decimal("12")},  # GST + HST/combined
    "MX": {Decimal("16"), Decimal("8")},
}

# Categories that express a zero / reverse-charge / exempt supply — always
# plausible at 0.00 regardless of the country's standard rate set.
_ZERO_RATE_CATEGORIES = {"Z", "AE", "E", "G", "O", "K"}

_RATE_TOLERANCE = Decimal("0.01")


def _norm_country(country_code: str | None) -> str | None:
    if not country_code:
        return None
    return country_code.strip().upper()


def validate_tax_id(country_code: str | None, tax_id: str | None) -> str | None:
    """Validate a tax-registration id's FORMAT for its country.

    Returns a reason code (never the value):
      * ``None`` — valid, OR the country is unknown/unsupported (skip), OR
        no tax id is present (absence is a structural concern, not a format one).
      * ``"malformed"`` — the country IS known and the id does not match.
    """
    country = _norm_country(country_code)
    if country is None:
        return None
    pattern = _TAX_ID_PATTERNS.get(country)
    if pattern is None:
        return None  # unsupported country — skip, never reject.
    if not tax_id:
        return None
    candidate = tax_id.strip().upper().replace(" ", "")
    return None if pattern.match(candidate) else "malformed"


def validate_tax_rate(
    country_code: str | None, rate: Decimal | None, category: str | None = None
) -> str | None:
    """Validate a tax rate's plausibility for its country/regime.

    Returns ``"implausible"`` when the rate is outside the country's known set
    (beyond a rounding tolerance), else ``None``. Zero-rate / reverse-charge /
    exempt categories are plausible at 0.00. Unknown country → skip.
    """
    if rate is None:
        return None
    country = _norm_country(country_code)
    if country is None:
        return None
    known = _TAX_RATES.get(country)
    if known is None:
        return None  # unsupported country — skip.

    cat = (category or "").strip().upper()
    if rate == Decimal("0") and (cat in _ZERO_RATE_CATEGORIES or not cat):
        return None
    # A reverse-charge / exempt line is plausible at 0 even with an explicit
    # category; a non-zero rate on such a category is the seller's choice and
    # we don't second-guess it against the standard set.
    if cat in _ZERO_RATE_CATEGORIES:
        return None

    for plausible in known | {Decimal("0")}:
        if abs(rate - plausible) <= _RATE_TOLERANCE:
            return None
    return "implausible"


def validate_tax_document(doc: EInvoiceDocument) -> list[FieldError]:
    """Country tax checks over the seller/buyer tax ids + each tax line.

    Field-path + code only — no PII. Reuses :class:`FieldError`.
    """
    errors: list[FieldError] = []

    seller_code = validate_tax_id(doc.seller.country_code, doc.seller.tax_id)
    if seller_code:
        errors.append(
            FieldError("seller.tax_id", seller_code, "Seller tax id format is invalid for country")
        )

    buyer_code = validate_tax_id(doc.buyer.country_code, doc.buyer.tax_id)
    if buyer_code:
        errors.append(
            FieldError("buyer.tax_id", buyer_code, "Buyer tax id format is invalid for country")
        )

    # Tax-rate plausibility — assess each tax line against the seller's regime
    # (the supplier's country drives the applicable VAT/GST/IVA rate).
    for i, tax in enumerate(doc.taxes):
        rate_code = validate_tax_rate(doc.seller.country_code, tax.rate, tax.category)
        if rate_code:
            errors.append(
                FieldError(
                    f"taxes[{i}].rate",
                    rate_code,
                    "Tax rate is implausible for the country/regime",
                )
            )

    return errors
