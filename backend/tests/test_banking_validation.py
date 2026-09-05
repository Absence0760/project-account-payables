"""IBAN + SWIFT/BIC validators.

The validators are a *structural* gate at the orchestration layer:
catching a malformed IBAN here saves a network round-trip to the
processor and surfaces a friendlier error to the AP team. False
positives (rejecting a valid IBAN) are worse than false negatives —
the processor is the ultimate arbiter, so anything that passes the
checksum should be passed through.

The mod-97 checksum is the load-bearing assertion. A regression that
skipped or misimplemented it would let typos through, which the
processor would reject after submission — and that's a money-path
slip (the payment is queued in "submitted" until the rejection
webhook lands, then bounces back to `failed`).
"""

from __future__ import annotations

import pytest

from app.schemas.vendor import validate_bank_routing_fields
from app.utils.banking import (
    SEPA_COUNTRIES,
    country_from_iban,
    is_sepa_country,
    validate_aba_routing,
    validate_iban,
    validate_swift_bic,
    validate_uk_account_number,
    validate_uk_sort_code,
)

# Known-good IBANs from the ISO test set + a few real-world examples
# that pass mod-97. The DE example is the standard ECB test number.
_VALID_IBANS = [
    "DE89 3704 0044 0532 0130 00",  # Germany, ECB test
    "GB82 WEST 1234 5698 7654 32",  # UK, ECB test
    "FR14 2004 1010 0505 0001 3M02 606",  # France, ECB test
    "ES91 2100 0418 4502 0005 1332",  # Spain, ECB test
    "NL91ABNA0417164300",  # Netherlands
    "BE68539007547034",  # Belgium
    "CH9300762011623852957",  # Switzerland
    "AT611904300234573201",  # Austria
    "IT60X0542811101000000123456",  # Italy
    "PT50000201231234567890154",  # Portugal
]


@pytest.mark.parametrize("iban", _VALID_IBANS)
def test_valid_iban_passes_mod97_check(iban):
    """Every IBAN in the ECB test set + a handful of real-world ones
    must validate. A regression that returned False on a known-good
    IBAN would block every European payment."""
    assert validate_iban(iban) is True


@pytest.mark.parametrize(
    "iban",
    [
        None,
        "",
        "   ",
        "DE",  # too short
        "DE0037040044053201300",  # bad checksum (one digit off)
        "DE89 3704 0044 0532 0130 01",  # last digit flipped
        "XX89 3704 0044 0532 0130 00",  # unknown country code
        "1289 3704 0044 0532 0130 00",  # country code is digits
        "DE89 3704 0044 0532 0130",  # too short for DE
        "DE89370400440532013000A",  # wrong length for DE
        "!!!INVALID!!!",
    ],
)
def test_invalid_iban_rejected(iban):
    """Empty input, bad checksums, unknown country codes, and
    obviously non-alphanumeric input all fail validation."""
    assert validate_iban(iban) is False


def test_iban_spaces_are_ignored():
    """IBANs printed on documents are grouped in fours. The
    validator must strip every whitespace character — a regression
    that took the raw string would reject the printable form that
    every customer types in."""
    grouped = "DE89 3704 0044 0532 0130 00"
    no_space = "DE89370400440532013000"
    assert validate_iban(grouped) == validate_iban(no_space) is True


def test_iban_case_insensitive():
    """IBANs are case-insensitive on the wire. We uppercase before
    checksum."""
    lower = "de89370400440532013000"
    assert validate_iban(lower) is True


def test_country_from_iban_extracts_two_letter_prefix():
    assert country_from_iban("DE89370400440532013000") == "DE"
    assert country_from_iban("gb82 west 1234 5698 7654 32") == "GB"


def test_country_from_iban_handles_garbage():
    assert country_from_iban(None) is None
    assert country_from_iban("") is None
    assert country_from_iban("X") is None
    assert country_from_iban("12") is None  # digits aren't a country


# ---------------------------------------------------------------------------
# SWIFT/BIC.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bic",
    [
        "DEUTDEFF",  # 8-char, Deutsche Bank Frankfurt
        "DEUTDEFFXXX",  # 11-char primary branch
        "BNPAFRPPXXX",  # 11-char BNP Paribas
        "CHASUS33",  # 8-char Chase USA
        "BARCGB22",  # 8-char Barclays UK
    ],
)
def test_valid_swift_bic_accepted(bic):
    assert validate_swift_bic(bic) is True


@pytest.mark.parametrize(
    "bic",
    [
        None,
        "",
        "ABCDEFG",  # too short
        "ABCDEFGHIJ",  # 10 chars (must be 8 or 11)
        "ABCDEFGHIJKL",  # 12 chars
        "1234DEFF",  # bank code must be letters
        "DEUT12FF",  # country code must be letters
        "DEUTDE0F",  # location starts with 0 (reserved test BIC)
        "DEUTDEFF!!!",  # non-alphanumeric branch
    ],
)
def test_invalid_swift_bic_rejected(bic):
    """Wrong length, wrong character classes, or '0' as the first
    location char (reserved for test BICs) all fail."""
    assert validate_swift_bic(bic) is False


def test_swift_bic_is_case_insensitive():
    assert validate_swift_bic("deutdeff") is True
    assert validate_swift_bic("DeUtDeFf") is True


# ---------------------------------------------------------------------------
# SEPA zone membership.
# ---------------------------------------------------------------------------


def test_sepa_country_includes_core_eurozone():
    """Spot-check a few key countries — full membership is in
    SEPA_COUNTRIES."""
    for cc in ("DE", "FR", "IT", "ES", "NL", "IE", "BE", "PT"):
        assert is_sepa_country(cc) is True, f"{cc} should be SEPA"


def test_sepa_country_is_case_insensitive():
    assert is_sepa_country("de") is True
    assert is_sepa_country("De") is True


def test_sepa_country_rejects_non_sepa():
    """US, Brazil, Japan etc. are not SEPA."""
    for cc in ("US", "BR", "JP", "AU", "CA", "MX", "IN"):
        assert is_sepa_country(cc) is False, f"{cc} should NOT be SEPA"


def test_sepa_country_handles_none_and_empty():
    assert is_sepa_country(None) is False
    assert is_sepa_country("") is False


def test_sepa_country_set_is_a_frozenset():
    """SEPA_COUNTRIES must be a frozenset so a typo elsewhere
    can't mutate the membership list at runtime."""
    assert isinstance(SEPA_COUNTRIES, frozenset)


# ---------------------------------------------------------------------------
# ABA / routing-transit number
# ---------------------------------------------------------------------------

# Real-world routing numbers (all pass the standard checksum).
_VALID_ABA_ROUTING = [
    "021000021",  # JPMorgan Chase, NY
    "011401533",
    "111000025",
    "091000019",
    "026009593",
    "122105155",
    "121000248",
]


@pytest.mark.parametrize("routing", _VALID_ABA_ROUTING)
def test_valid_aba_routing_passes_checksum(routing):
    assert validate_aba_routing(routing) is True


@pytest.mark.parametrize(
    "routing",
    [
        None,
        "",
        "   ",
        "12345678",  # 8 digits — too short
        "1234567890",  # 10 digits — too long
        "021000020",  # last digit off by one (breaks the checksum)
        "02100002X",  # non-digit
        "!!!!!!!!!",
    ],
)
def test_invalid_aba_routing_rejected(routing):
    assert validate_aba_routing(routing) is False


def test_aba_routing_spaces_are_ignored():
    """Some UIs group a routing number for readability."""
    assert validate_aba_routing("021 000 021") == validate_aba_routing("021000021") is True


# ---------------------------------------------------------------------------
# UK sort code + account number
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sort_code",
    [
        "200000",  # bare 6 digits
        "20-00-00",  # grouped, the printed/UI form
        "12-34-56",
        "999999",
    ],
)
def test_valid_uk_sort_code_accepted(sort_code):
    assert validate_uk_sort_code(sort_code) is True


@pytest.mark.parametrize(
    "sort_code",
    [
        None,
        "",
        "   ",
        "12345",  # 5 digits — too short
        "1234567",  # 7 digits — too long
        "20-00-0",  # grouped but a group is short
        "2000-00",  # grouped in the wrong places
        "20-0X-00",  # non-digit
        "AB-CD-EF",
        "!!!!!!",
    ],
)
def test_invalid_uk_sort_code_rejected(sort_code):
    assert validate_uk_sort_code(sort_code) is False


def test_uk_sort_code_no_checksum_just_shape():
    """Unlike ABA routing, a sort code has no public checksum — any
    well-formed 6-digit value passes; the bank/processor is the arbiter of
    whether it's a real branch."""
    assert validate_uk_sort_code("000000") is True
    assert validate_uk_sort_code("123456") is True


@pytest.mark.parametrize(
    "account_number",
    [
        "12345678",
        "00000000",
        "99999999",
    ],
)
def test_valid_uk_account_number_accepted(account_number):
    assert validate_uk_account_number(account_number) is True


@pytest.mark.parametrize(
    "account_number",
    [
        None,
        "",
        "   ",
        "1234567",  # 7 digits — too short
        "123456789",  # 9 digits — too long
        "1234567X",  # non-digit
        "!!!!!!!!",
    ],
)
def test_invalid_uk_account_number_rejected(account_number):
    assert validate_uk_account_number(account_number) is False


def test_uk_account_number_spaces_are_ignored():
    assert validate_uk_account_number("1234 5678") == validate_uk_account_number("12345678") is True


# --- The gate itself -------------------------------------------------------
#
# `validate_uk_account_number` existed, was tested, and was reached by nothing
# in production: `validate_bank_routing_fields` — the single chokepoint every
# bank-detail write goes through, including `approve_change_request`, where the
# dual-control BEC sign-off is applied — checked the sort code and left the
# account number unvalidated. These assert the pair is now validated together,
# and that a non-UK payee is still accepted.


def test_gate_accepts_a_well_formed_uk_payee():
    details = {"sort_code": "12-34-56", "account_number": "12345678"}
    assert validate_bank_routing_fields(details) is details


def test_gate_rejects_a_uk_payee_whose_account_number_is_short():
    # A valid sort code alongside a 5-digit account number used to clear both
    # staging and the second-approver sign-off.
    with pytest.raises(ValueError) as exc:
        validate_bank_routing_fields({"sort_code": "123456", "account_number": "12345"})
    assert "account_number" in str(exc.value)
    # Banking data never reaches the message — the field name only.
    assert "12345" not in str(exc.value)


def test_gate_leaves_a_non_uk_account_number_alone():
    # No sort code → not a UK payee. A US/IBAN `account_number` is not 8 digits
    # and must not be refused.
    details = {"routing_number": "021000021", "account_number": "000123456789"}
    assert validate_bank_routing_fields(details) is details


def test_gate_still_rejects_a_bad_sort_code_before_the_account_number():
    with pytest.raises(ValueError) as exc:
        validate_bank_routing_fields({"sort_code": "12345", "account_number": "12345678"})
    assert "sort_code" in str(exc.value)
