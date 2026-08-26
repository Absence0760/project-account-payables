"""Ambiguous-date disambiguation (`app/utils/dates.py`).

`03/04/2026` is genuinely ambiguous — both "3 April" (day-first) and "March 4"
(month-first) are structurally valid, and nothing in the string itself can
settle it. Before this module existed, four call sites each hand-rolled their
own try/except format order, and all four tried month-first before day-first —
so a UK invoice dated `03/04/2026` silently booked as March 4th instead of
April 3rd. No error, just a wrong date.

`parse_ambiguous_date` is the single shared disambiguator: it tries the
caller-supplied `day_first` preference first, falls back to the other order
only when the preferred one is structurally invalid for that string, and
returns `None` — never guesses — when neither order parses.
`resolve_day_first_preference` derives that preference from the one org
signal that unambiguously means "this org is UK-registered": a non-empty
`company.companies_house_number` (Companies House registers UK entities
only — unlike `vat_registration_number`, which plenty of non-UK, non-day-first
countries also have).

This file also drift-guards the four call sites — `services/extraction.py`,
`services/csv_import.py`, `services/bank_reconciliation.py`,
`services/vendor_statement_recon.py` — so none of them can quietly grow a
second, hand-rolled `"%m/%d/%Y"` / `"%d/%m/%Y"` try/except pair that
reintroduces the divergence the shared helper exists to prevent. Shape
borrowed from `tests/test_utc_today.py`'s `UTC_TODAY_MODULES` guard.
"""

from __future__ import annotations

import ast
import pathlib
from datetime import date

import pytest

from app.utils.dates import parse_ambiguous_date, resolve_day_first_preference

APP_DIR = pathlib.Path(__file__).resolve().parents[1] / "app"

# Modules whose date parsing must route the DD/MM-vs-MM/DD case through the
# shared `parse_ambiguous_date` helper rather than a hand-rolled try/except
# order. Add to this list when a new call site faces the same ambiguity;
# never remove from it.
AMBIGUOUS_DATE_CALL_SITES = (
    "services/extraction.py",
    "services/csv_import.py",
    "services/bank_reconciliation.py",
    "services/vendor_statement_recon.py",
)

# The literal format-string pair that, together in one module, is exactly the
# bug this helper fixes: trying one fixed order for an ambiguous numeric date
# with no locale signal. These strings should only ever appear inside
# `app/utils/dates.py` itself.
_AMBIGUOUS_FORMAT_LITERALS = {"%m/%d/%Y", "%d/%m/%Y", "%m-%d-%Y", "%d-%m-%Y"}


def _string_literals(source: str) -> set[str]:
    tree = ast.parse(source)
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


class TestParseAmbiguousDate:
    def test_none_and_empty_return_none(self):
        assert parse_ambiguous_date(None, day_first=False) is None
        assert parse_ambiguous_date("", day_first=False) is None
        assert parse_ambiguous_date("   ", day_first=True) is None

    def test_no_recognised_separator_returns_none(self):
        # Dots are a different (non-ambiguous-by-this-helper) convention;
        # this helper only disambiguates slash/dash numeric dates.
        assert parse_ambiguous_date("03.04.2026", day_first=False) is None
        assert parse_ambiguous_date("not a date", day_first=True) is None

    def test_genuinely_ambiguous_date_month_first_preference(self):
        """`03/04/2026` — both orders are structurally valid. With no UK
        signal (day_first=False), it reads as the pre-existing US
        convention: March 4th."""
        assert parse_ambiguous_date("03/04/2026", day_first=False) == date(2026, 3, 4)

    def test_genuinely_ambiguous_date_day_first_preference(self):
        """The SAME string, with the org's day-first signal set, reads as
        3 April instead — this is the bug fix: a UK org's `03/04/2026` is
        no longer silently corrupted into March 4th."""
        assert parse_ambiguous_date("03/04/2026", day_first=True) == date(2026, 4, 3)

    def test_structurally_invalid_in_month_first_falls_back_to_day_first(self):
        """`25/03/2026` cannot be MM/DD (there is no 25th month) — so even
        with day_first=False (the "prefer month-first" default), it falls
        back to the only structurally valid reading: 25 March 2026."""
        assert parse_ambiguous_date("25/03/2026", day_first=False) == date(2026, 3, 25)

    def test_structurally_invalid_in_day_first_falls_back_to_month_first(self):
        """Mirror case: `03/25/2026` cannot be DD/MM (there is no 25th
        month either way you read it), so even with day_first=True it falls
        back to the only valid reading: March 25, 2026."""
        assert parse_ambiguous_date("03/25/2026", day_first=True) == date(2026, 3, 25)

    def test_invalid_in_both_orders_returns_none(self):
        """`13/13/2026` — neither DD/MM nor MM/DD has a 13th month. Never
        guess; report unparseable."""
        assert parse_ambiguous_date("13/13/2026", day_first=False) is None
        assert parse_ambiguous_date("13/13/2026", day_first=True) is None

    def test_dash_separated_variant_disambiguates_the_same_way(self):
        assert parse_ambiguous_date("03-04-2026", day_first=False) == date(2026, 3, 4)
        assert parse_ambiguous_date("03-04-2026", day_first=True) == date(2026, 4, 3)

    def test_whitespace_is_stripped(self):
        assert parse_ambiguous_date("  03/04/2026  ", day_first=True) == date(2026, 4, 3)


class TestResolveDayFirstPreference:
    def test_none_settings_defaults_false(self):
        assert resolve_day_first_preference(None) is False

    def test_empty_settings_defaults_false(self):
        assert resolve_day_first_preference({}) is False

    def test_company_block_with_no_companies_house_number_defaults_false(self):
        assert resolve_day_first_preference({"company": {}}) is False

    def test_blank_companies_house_number_defaults_false(self):
        assert resolve_day_first_preference({"company": {"companies_house_number": "   "}}) is False

    def test_vat_registration_number_alone_does_not_trigger_day_first(self):
        """A VAT number is common well outside the UK (and outside
        day-first countries generally) — it must NOT be read as a UK
        signal. Only `companies_house_number` does that."""
        settings = {"company": {"vat_registration_number": "GB123456789"}}
        assert resolve_day_first_preference(settings) is False

    def test_companies_house_number_present_triggers_day_first(self):
        settings = {"company": {"companies_house_number": "12345678"}}
        assert resolve_day_first_preference(settings) is True

    def test_companies_house_number_alongside_vat_still_triggers(self):
        settings = {
            "company": {
                "companies_house_number": "12345678",
                "vat_registration_number": "GB123456789",
            }
        }
        assert resolve_day_first_preference(settings) is True

    def test_malformed_settings_shapes_never_raise(self):
        assert resolve_day_first_preference({"company": "not-a-dict"}) is False
        assert resolve_day_first_preference({"company": None}) is False


class TestAmbiguousDateCallSitesConverged:
    """Every call site that used to hand-roll its own MM/DD-vs-DD/MM order
    must now route through the shared helper, and none of them may
    reintroduce the old hardcoded format-literal pair."""

    @pytest.mark.parametrize("relative", AMBIGUOUS_DATE_CALL_SITES)
    def test_module_imports_the_shared_helper(self, relative):
        path = APP_DIR / relative
        assert path.exists(), f"{relative} moved — update AMBIGUOUS_DATE_CALL_SITES"
        source = path.read_text(encoding="utf-8")
        assert "parse_ambiguous_date" in source, (
            f"{relative} used to face the DD/MM-vs-MM/DD ambiguity directly — it "
            "must resolve it via app.utils.dates.parse_ambiguous_date, not its own "
            "try/except format order."
        )

    @pytest.mark.parametrize("relative", AMBIGUOUS_DATE_CALL_SITES)
    def test_module_does_not_hardcode_the_ambiguous_format_pair(self, relative):
        path = APP_DIR / relative
        literals = _string_literals(path.read_text(encoding="utf-8"))
        offenders = literals & _AMBIGUOUS_FORMAT_LITERALS
        assert not offenders, (
            f"{relative} hardcodes {offenders} — the ambiguous DD/MM-vs-MM/DD "
            "case must be resolved by app.utils.dates.parse_ambiguous_date "
            "(org-locale-aware), never a fixed try/except order in the call site."
        )
