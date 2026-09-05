"""Code-list membership for EN 16931 / PEPPOL BIS Billing 3.0 (BR-CL-* rules).

Several EN 16931 fields are **coded**: their content has to be a member of a
published external list, not free text. A value outside its list is not a
stylistic problem — the receiver's Schematron rejects the document, and until
this module existed :func:`app.services.e_invoice.bis3.bis3_conformance_errors`
would happily wave such a document through and let the generator stamp a
conformance claim on it.

**What is enforced, and what deliberately is not.**

Membership is only asserted where we hold the *complete* list. A partial list
turns a legitimate rare code into a false refusal, and the BIS gate is not
advisory: :func:`app.services.peppol_send.send_invoice_over_peppol` calls
``assert_bis3_conformant`` and 422s. Refusing a valid document is a worse
failure than the gap it would close, so a list we can only hold partially gets a
*structural* check (could this string be a member at all?) and no membership
claim.

============================  ==========  ==================================
Field                         Rule        Status here
============================  ==========  ==================================
Invoice type code (BT-3)      BR-CL-01    Enforced — UNTDID 1001, EN 16931
                                          invoice + credit-note subset.
Document currency (BT-5)      BR-CL-03    Enforced — ISO 4217 alphabetic.
Country code (BT-40 / BT-55)  BR-CL-14    Enforced — ISO 3166-1 alpha-2.
VAT category, breakdown       BR-CL-17    Enforced — UNCL5305 EN 16931 subset.
  (BT-118)
VAT category, line (BT-151)   BR-CL-18    Enforced — same subset.
Unit of measure (BT-130)      BR-CL-23    **Shape only.** UN/ECE Rec 20 + 21
                                          is thousands of codes; we hold no
                                          complete copy (the official code
                                          list is deliberately not vendored),
                                          so an unrecognised-but-well-formed
                                          code is accepted.
Payment means (BT-81)         BR-CL-16    **Shape only**, same reason —
                                          ``payment_means.py`` holds the seven
                                          UNCL4461 codes we map, not the list.
EAS scheme id (BT-34-1)       BR-CL-25    **Not checked.** The CEF EAS list is
                                          maintained outside the standard and
                                          changes between releases.
============================  ==========  ==================================

The currency and country tables lean deliberately **inclusive**: recently
withdrawn codes that still appear on real documents are kept. Over-inclusion
only weakens detection; under-inclusion would make us refuse a document that
does conform, which is the failure this module must not have.

PII invariant: nothing here ever returns, logs, or embeds a field *value* — the
caller builds a :class:`app.services.e_invoice.validate.FieldError` naming the
field path and the rule id only.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# ISO 4217 — alphabetic currency codes (BR-CL-03).
#
# The active list plus a short tail of codes withdrawn recently enough to still
# arrive on a real invoice (HRK, SLL, ZWL, CUC, VEF, MRO, STD, BYR, LTL, LVL).
# Includes the non-country "X" codes (XAU, XDR, XXX …) because ISO 4217 defines
# them; a document denominated in XXX fails elsewhere, not here.
# ---------------------------------------------------------------------------
ISO_4217_CURRENCIES: frozenset[str] = frozenset(
    """
    AED AFN ALL AMD ANG AOA ARS AUD AWG AZN
    BAM BBD BDT BGN BHD BIF BMD BND BOB BOV BRL BSD BTN BWP BYN BZD
    CAD CDF CHE CHF CHW CLF CLP CNY COP COU CRC CUP CVE CZK
    DJF DKK DOP DZD EGP ERN ETB EUR
    FJD FKP GBP GEL GHS GIP GMD GNF GTQ GYD
    HKD HNL HTG HUF IDR ILS INR IQD IRR ISK
    JMD JOD JPY KES KGS KHR KMF KPW KRW KWD KYD KZT
    LAK LBP LKR LRD LSL LYD
    MAD MDL MGA MKD MMK MNT MOP MRU MUR MVR MWK MXN MXV MYR MZN
    NAD NGN NIO NOK NPR NZD OMR
    PAB PEN PGK PHP PKR PLN PYG QAR RON RSD RUB RWF
    SAR SBD SCR SDG SEK SGD SHP SLE SOS SRD SSP STN SVC SYP SZL
    THB TJS TMT TND TOP TRY TTD TWD TZS
    UAH UGX USD USN UYI UYU UYW UZS VED VES VND VUV
    WST XAF XAG XAU XBA XBB XBC XBD XCD XCG XDR XOF XPD XPF XPT XSU XTS XUA XXX
    YER ZAR ZMW ZWG
    BYR CUC HRK LTL LVL MRO SLL STD VEF ZWL
    """.split()
)

# ---------------------------------------------------------------------------
# ISO 3166-1 alpha-2 — country codes (BR-CL-14).
#
# All currently assigned codes. ``1A`` is included because PEPPOL uses it for
# Kosovo, which has no ISO code; ``EL`` is deliberately NOT here — it is a VAT
# prefix, not a country code (Greece is ``GR``), and accepting it would let a
# real BR-CL-14 violation through.
# ---------------------------------------------------------------------------
ISO_3166_ALPHA2: frozenset[str] = frozenset(
    """
    AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ
    BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ
    CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ
    DE DJ DK DM DO DZ EC EE EG EH ER ES ET
    FI FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY
    HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT
    JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ
    LA LB LC LI LK LR LS LT LU LV LY
    MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ
    NA NC NE NF NG NI NL NO NP NR NU NZ OM
    PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW
    SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ
    TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ
    UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW
    1A
    """.split()
)

# ---------------------------------------------------------------------------
# UNCL5305 — VAT category codes, the EN 16931 subset (BR-CL-17 / BR-CL-18).
#
# This list IS complete: EN 16931 restricts UNCL5305 to exactly these nine.
# (Italy's ``B`` "transferred VAT" is in the raw UN list but not in the EN
# subset, so a document using it genuinely cannot claim the profile.)
# ---------------------------------------------------------------------------
UNCL5305_VAT_CATEGORIES: frozenset[str] = frozenset(
    {
        "AE",  # VAT reverse charge
        "E",  # Exempt from tax
        "G",  # Free export item, VAT not charged
        "K",  # VAT exempt for EEA intra-community supply
        "L",  # Canary Islands general indirect tax (IGIC)
        "M",  # Ceuta and Melilla production/services/importation tax (IPSI)
        "O",  # Services outside scope of tax
        "S",  # Standard rate
        "Z",  # Zero rated goods
    }
)

#: The categories EN 16931 requires to carry a **zero** rate (BR-Z-05, BR-E-05,
#: BR-AE-05, BR-G-05, BR-O-05). ``S`` is the mirror image — BR-S-05 requires a
#: rate greater than zero. ``K`` (intra-community) also requires zero (BR-IC-05).
ZERO_RATE_VAT_CATEGORIES: frozenset[str] = frozenset({"AE", "E", "G", "K", "O", "Z"})

#: Per-category rule-id families, so an error names the rule the receiver's
#: validator will name. Keyed by UNCL5305 code; the value is the ``BR-<x>``
#: infix (BR-S-05 / BR-S-08 …).
VAT_CATEGORY_RULE_INFIX: dict[str, str] = {
    "S": "S",
    "Z": "Z",
    "E": "E",
    "AE": "AE",
    "K": "IC",
    "G": "G",
    "O": "O",
    "L": "IG",
    "M": "IP",
}

# ---------------------------------------------------------------------------
# UNTDID 1001 — document type code (BR-CL-01).
#
# The EN 16931 invoice + credit-note subset. Kept generous on purpose: the
# tighter PEPPOL BIS restriction (which allows only a handful of these on a
# Billing 3.0 invoice) is NOT enforced here — see the module docstring.
# ---------------------------------------------------------------------------
UNTDID_1001_DOCUMENT_TYPES: frozenset[str] = frozenset(
    """
    71 80 81 82 83 84 102 130 202 203 204 211 218 219 261 262 295 296 308 320
    325 326 331 380 381 382 383 384 385 386 387 388 389 390 393 394 395 396
    420 456 457 458 527 553 575 623 633 751 780 817 870 875 876 877 935
    """.split()
)

# ---------------------------------------------------------------------------
# Shape-only guards for the lists we do not hold.
# ---------------------------------------------------------------------------

#: UN/ECE Recommendation 20 / 21 unit codes are 1-3 characters drawn from
#: upper-case letters and digits (``C62``, ``HUR``, ``KGM``, ``H87``, ``MTK``,
#: and the Rec 21 ``X``-prefixed packaging codes). Anything else cannot be a
#: member of the list at all, whatever the list's exact contents.
_UNIT_CODE_SHAPE = re.compile(r"^[A-Z0-9]{1,3}$")

#: UNCL4461 payment-means codes are 1-3 digits, plus the ``ZZZ`` mutually-
#: defined escape.
_PAYMENT_MEANS_SHAPE = re.compile(r"^(\d{1,3}|ZZZ)$")


def _norm(value: str | None) -> str | None:
    """Upper-case + strip. ``None``/blank in, ``None`` out (the caller decides
    whether absence is itself an error — a code list says nothing about it)."""
    if value is None:
        return None
    stripped = value.strip().upper()
    return stripped or None


def is_valid_currency(code: str | None) -> bool:
    """ISO 4217 alphabetic membership. Absent → True (not this rule's business)."""
    normalized = _norm(code)
    return normalized is None or normalized in ISO_4217_CURRENCIES


def is_valid_country(code: str | None) -> bool:
    """ISO 3166-1 alpha-2 membership. Absent → True."""
    normalized = _norm(code)
    return normalized is None or normalized in ISO_3166_ALPHA2


def is_valid_vat_category(code: str | None) -> bool:
    """UNCL5305 (EN 16931 subset) membership. Absent → True."""
    normalized = _norm(code)
    return normalized is None or normalized in UNCL5305_VAT_CATEGORIES


def is_valid_document_type(code: str | None) -> bool:
    """UNTDID 1001 (EN 16931 subset) membership. Absent → True."""
    normalized = _norm(code)
    return normalized is None or normalized in UNTDID_1001_DOCUMENT_TYPES


def is_plausible_unit_code(code: str | None) -> bool:
    """Shape check only — see the module docstring. Absent → True."""
    normalized = _norm(code)
    return normalized is None or bool(_UNIT_CODE_SHAPE.match(normalized))


def is_plausible_payment_means(code: str | None) -> bool:
    """Shape check only — see the module docstring. Absent → True."""
    normalized = _norm(code)
    return normalized is None or bool(_PAYMENT_MEANS_SHAPE.match(normalized))


def vat_category_rule(code: str | None, suffix: str) -> str:
    """Rule id for a per-VAT-category rule, e.g. ``("Z", "05") -> "BR-Z-05"``.

    An unrecognised category has no family, so it falls back to the code-list
    rule that will already have flagged it.
    """
    infix = VAT_CATEGORY_RULE_INFIX.get(_norm(code) or "")
    return f"BR-{infix}-{suffix}" if infix else "BR-CL-18"
