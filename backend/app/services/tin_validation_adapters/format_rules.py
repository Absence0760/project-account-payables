"""Deterministic, offline TIN format + structural validation.

Shared by every TIN-validation adapter — the ``mock`` adapter is *only*
these rules, and the ``tax1099`` partner adapter runs them first (no point
spending an API call on a malformed TIN) before the online TIN-match.

These are the structural checks the IRS publishes for EINs and SSNs. They
catch typos and obviously-fabricated numbers; they do **not** prove the
number is assigned to anyone — only an IRS TIN-match call does that.

No raw TIN ever leaves this module: callers get back a normalised digit
string for the online lookup, but the public result objects only carry the
last-4. Keep it that way.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# IRS EIN prefixes (first two digits of the 9-digit EIN) that have never
# been issued by any campus. An EIN whose prefix is in this set is
# structurally invalid. Source: IRS "Valid EIN Prefixes" table.
_INVALID_EIN_PREFIXES = frozenset(
    {"00", "07", "08", "09", "17", "18", "19", "28", "29", "49", "78", "79", "89"}
)

_TIN_DIGITS = re.compile(r"\d")


@dataclass(frozen=True)
class FormatCheck:
    """Result of structural validation. ``digits`` is the 9-digit string
    (no separators) for an online lookup — never persisted, never logged."""

    ok: bool
    tin_type: str | None  # "ein" | "ssn" | None
    digits: str | None
    reason_code: str | None = None

    @property
    def last4(self) -> str | None:
        return self.digits[-4:] if self.digits and len(self.digits) >= 4 else None


def normalize_digits(raw: str) -> str:
    """Strip everything that isn't a digit."""
    return "".join(_TIN_DIGITS.findall(raw or ""))


def _ein_format_ok(digits: str) -> tuple[bool, str | None]:
    # EIN: NN-NNNNNNN. Reject never-issued prefixes and all-zero bodies.
    if digits[:2] in _INVALID_EIN_PREFIXES:
        return False, "ein_invalid_prefix"
    if digits == "0" * 9:
        return False, "all_zeros"
    return True, None


def _ssn_format_ok(digits: str) -> tuple[bool, str | None]:
    # SSN: AAA-GG-SSSS. The SSA never issues:
    #   - area "000", "666", or 900-999 (the 9xx range is reserved for ITINs,
    #     which are themselves valid TINs, so we allow 900-999 here);
    #   - group "00";
    #   - serial "0000".
    area, group, serial = digits[:3], digits[3:5], digits[5:]
    if area in {"000", "666"}:
        return False, "ssn_invalid_area"
    if group == "00":
        return False, "ssn_invalid_group"
    if serial == "0000":
        return False, "ssn_invalid_serial"
    return True, None


def check_format(tin: str, tin_type_hint: str | None = None) -> FormatCheck:
    """Validate a TIN's structure offline.

    ``tin_type_hint`` (``"ein"`` / ``"ssn"``) disambiguates which ruleset to
    apply. Without it we infer from the original separator shape (``NN-`` →
    EIN, ``NNN-NN-`` → SSN); failing that we default to EIN rules, which are
    the common AP case (most 1099 vendors are businesses).
    """
    digits = normalize_digits(tin)
    if len(digits) != 9:
        return FormatCheck(ok=False, tin_type=None, digits=None, reason_code="format_invalid")

    hint = (tin_type_hint or "").lower().strip()
    if hint not in {"ein", "ssn"}:
        # Infer from separator placement in the original string.
        compact = (tin or "").strip()
        if re.fullmatch(r"\d{2}-\d{7}", compact):
            hint = "ein"
        elif re.fullmatch(r"\d{3}-\d{2}-\d{4}", compact):
            hint = "ssn"
        else:
            hint = "ein"

    if hint == "ssn":
        ok, reason = _ssn_format_ok(digits)
        return FormatCheck(ok=ok, tin_type="ssn", digits=digits, reason_code=reason)

    ok, reason = _ein_format_ok(digits)
    return FormatCheck(ok=ok, tin_type="ein", digits=digits, reason_code=reason)
