"""Banking identifier validation — IBAN and SWIFT/BIC.

Used by the international payments path to refuse a payment dispatch
when the vendor's bank fields don't pass a structural check. Catching
a malformed IBAN at the orchestration layer is cheap; catching it at
the processor's API costs a round trip and surfaces a less helpful
error to the AP team.

These are *structural* checks only — a valid checksum doesn't mean
the account exists. The processor verifies the rest.
"""

from __future__ import annotations

# ISO-13616 IBAN lengths by country. List is non-exhaustive but covers
# the corridors we care about today; an unknown country code falls back
# to a permissive length-only check. Extend as new corridors come online.
_IBAN_LENGTHS: dict[str, int] = {
    "AD": 24,
    "AE": 23,
    "AL": 28,
    "AT": 20,
    "AZ": 28,
    "BA": 20,
    "BE": 16,
    "BG": 22,
    "BH": 22,
    "BR": 29,
    "BY": 28,
    "CH": 21,
    "CR": 22,
    "CY": 28,
    "CZ": 24,
    "DE": 22,
    "DK": 18,
    "DO": 28,
    "EE": 20,
    "EG": 29,
    "ES": 24,
    "FI": 18,
    "FO": 18,
    "FR": 27,
    "GB": 22,
    "GE": 22,
    "GI": 23,
    "GL": 18,
    "GR": 27,
    "GT": 28,
    "HR": 21,
    "HU": 28,
    "IE": 22,
    "IL": 23,
    "IQ": 23,
    "IS": 26,
    "IT": 27,
    "JO": 30,
    "KW": 30,
    "KZ": 20,
    "LB": 28,
    "LC": 32,
    "LI": 21,
    "LT": 20,
    "LU": 20,
    "LV": 21,
    "MC": 27,
    "MD": 24,
    "ME": 22,
    "MK": 19,
    "MR": 27,
    "MT": 31,
    "MU": 30,
    "NL": 18,
    "NO": 15,
    "PK": 24,
    "PL": 28,
    "PS": 29,
    "PT": 25,
    "QA": 29,
    "RO": 24,
    "RS": 22,
    "SA": 24,
    "SC": 31,
    "SE": 24,
    "SI": 19,
    "SK": 24,
    "SM": 27,
    "ST": 25,
    "SV": 28,
    "TL": 23,
    "TN": 24,
    "TR": 26,
    "UA": 29,
    "VA": 22,
    "VG": 24,
    "XK": 20,
}

# SEPA-zone country codes — these are the destinations that can be
# paid via SEPA Credit Transfer / SEPA Instant. Source of truth:
# https://www.europeanpaymentscouncil.eu/ — list of countries in scope.
SEPA_COUNTRIES: frozenset[str] = frozenset(
    {
        "AT",
        "BE",
        "BG",
        "CH",
        "CY",
        "CZ",
        "DE",
        "DK",
        "EE",
        "ES",
        "FI",
        "FR",
        "GB",
        "GI",
        "GR",
        "HR",
        "HU",
        "IE",
        "IS",
        "IT",
        "LI",
        "LT",
        "LU",
        "LV",
        "MC",
        "MT",
        "NL",
        "NO",
        "PL",
        "PT",
        "RO",
        "SE",
        "SI",
        "SK",
        "SM",
        "VA",
    }
)


def _normalize_iban(raw: str) -> str:
    """Uppercase + strip every whitespace character. IBANs printed on
    documents are typically grouped in fours; we have to collapse the
    spaces before doing anything else."""
    return "".join(raw.split()).upper()


def country_from_iban(iban: str | None) -> str | None:
    """Return the 2-letter country code from the start of an IBAN.

    None / too-short input returns None — callers can decide whether
    that's a hard failure or a fallback path."""
    if not iban:
        return None
    normalized = _normalize_iban(iban)
    if len(normalized) < 2:
        return None
    cc = normalized[:2]
    if not cc.isalpha():
        return None
    return cc


def validate_iban(iban: str | None) -> bool:
    """Validate an IBAN structurally.

    Three checks, in order:
      1. Country prefix is two letters, check digits are two digits.
      2. Length matches the country's IBAN length (if known); for
         unknown countries we accept 15–34 chars (the ISO range).
      3. Mod-97 checksum holds: move the first 4 chars to the end,
         translate letters to digits (A=10, B=11, ..., Z=35),
         interpret as an integer, take mod 97 — must equal 1.

    Empty / None / non-alphanumeric input is rejected.
    """
    if not iban:
        return False
    normalized = _normalize_iban(iban)
    if not normalized.isalnum():
        return False
    if len(normalized) < 15 or len(normalized) > 34:
        return False

    cc = normalized[:2]
    if not cc.isalpha():
        return False
    if not normalized[2:4].isdigit():
        return False

    expected_len = _IBAN_LENGTHS.get(cc)
    if expected_len is not None and len(normalized) != expected_len:
        return False

    # Move the first four characters to the end, then replace letters
    # with two-digit numerics (A=10 ... Z=35).
    rearranged = normalized[4:] + normalized[:4]
    digits_str = ""
    for ch in rearranged:
        if ch.isdigit():
            digits_str += ch
        else:
            digits_str += str(ord(ch) - 55)  # 'A' is 65; A=10

    try:
        as_int = int(digits_str)
    except ValueError:
        return False
    return as_int % 97 == 1


def validate_swift_bic(bic: str | None) -> bool:
    """Validate a SWIFT/BIC code structurally.

    ISO-9362 BIC layout: 8 or 11 characters total.
      - 4 letters: bank code
      - 2 letters: country code (ISO 3166-1 alpha-2)
      - 2 chars: location code (letters or digits, first NOT zero)
      - 3 chars (optional): branch code (letters or digits)

    We don't check that the bank/country combination exists; the
    processor will.
    """
    if not bic:
        return False
    code = "".join(bic.split()).upper()
    if len(code) not in (8, 11):
        return False
    if not code.isalnum():
        return False

    bank, country, location = code[:4], code[4:6], code[6:8]
    if not bank.isalpha():
        return False
    if not country.isalpha():
        return False
    # Location: 2 alphanumeric, first char must NOT be '0' (reserved
    # for test BICs); we allow '1' which is also reserved but used
    # in practice by some test environments.
    if location[0] == "0":
        return False
    if len(code) == 11:
        branch = code[8:]
        if not branch.isalnum():
            return False
    return True


def validate_aba_routing(routing_number: str | None) -> bool:
    """Validate a US ABA / routing-transit number structurally.

    Nine digits; the standard checksum (ABA Technical Bulletin 2003-1):

        3*(d1+d4+d7) + 7*(d2+d5+d8) + 1*(d3+d6+d9) ≡ 0 (mod 10)

    Same posture as `validate_iban` / `validate_swift_bic` above: this
    doesn't check the bank actually exists at the Fed, only that the
    number is well-formed. Catching a fat-fingered digit here is cheap;
    catching it at the bank costs a returned/misdirected ACH.
    """
    if not routing_number:
        return False
    digits = "".join(routing_number.split())
    if len(digits) != 9 or not digits.isdigit():
        return False
    d = [int(c) for c in digits]
    checksum = 3 * (d[0] + d[3] + d[6]) + 7 * (d[1] + d[4] + d[7]) + (d[2] + d[5] + d[8])
    return checksum % 10 == 0


def is_sepa_country(country_code: str | None) -> bool:
    """True iff the 2-letter country code is in the SEPA zone."""
    if not country_code:
        return False
    return country_code.upper() in SEPA_COUNTRIES
