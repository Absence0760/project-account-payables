"""Unit tests for the per-vendor / per-commodity match-rule resolver.

Pure logic — no DB, no network. Covers org-default fallback, vendor override,
commodity override, per-field fallthrough, malformed-config robustness, and
``vendor_id`` accepted as a ``uuid.UUID`` or ``str``.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from app.services.matching_rules import (
    DEFAULT_REQUIRE_INSPECTION,
    DEFAULT_TOLERANCE_PCT,
    resolve_match_rule,
)

_VENDOR_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")


def test_no_settings_uses_hardcoded_default():
    rule = resolve_match_rule(None, vendor_id=_VENDOR_ID, gl_account="6000")
    assert rule.require_inspection is DEFAULT_REQUIRE_INSPECTION
    assert rule.tolerance_pct == DEFAULT_TOLERANCE_PCT
    assert rule.source == "default"


def test_empty_matching_uses_hardcoded_default():
    rule = resolve_match_rule({"matching": {}}, vendor_id=None, gl_account=None)
    assert rule.require_inspection is False
    assert rule.tolerance_pct == 5.0
    assert rule.source == "default"


def test_org_default_applies():
    settings = {"matching": {"require_inspection": True, "tolerance_pct": 3.0}}
    rule = resolve_match_rule(settings, vendor_id=_VENDOR_ID, gl_account="6000")
    assert rule.require_inspection is True
    assert rule.tolerance_pct == 3.0
    assert rule.source == "org"


def test_vendor_rule_wins_over_org_and_commodity():
    settings = {
        "matching": {
            "require_inspection": False,
            "tolerance_pct": 5.0,
            "vendor_rules": {str(_VENDOR_ID): {"require_inspection": True, "tolerance_pct": 2.0}},
            "commodity_rules": {"6000": {"require_inspection": False, "tolerance_pct": 4.0}},
        }
    }
    rule = resolve_match_rule(settings, vendor_id=_VENDOR_ID, gl_account="6000")
    assert rule.require_inspection is True
    assert rule.tolerance_pct == 2.0
    assert rule.source == "vendor"


def test_commodity_rule_when_no_vendor_rule():
    settings = {
        "matching": {
            "require_inspection": False,
            "tolerance_pct": 5.0,
            "commodity_rules": {"6000": {"require_inspection": True, "tolerance_pct": 1.0}},
        }
    }
    rule = resolve_match_rule(settings, vendor_id=_VENDOR_ID, gl_account="6000")
    assert rule.require_inspection is True
    assert rule.tolerance_pct == 1.0
    assert rule.source == "commodity"


def test_per_field_fallthrough_vendor_inspection_only():
    # Vendor rule sets only require_inspection; tolerance_pct falls through to
    # the commodity rule, then would fall to org/default.
    settings = {
        "matching": {
            "require_inspection": False,
            "tolerance_pct": 5.0,
            "vendor_rules": {str(_VENDOR_ID): {"require_inspection": True}},
            "commodity_rules": {"6000": {"tolerance_pct": 1.5}},
        }
    }
    rule = resolve_match_rule(settings, vendor_id=_VENDOR_ID, gl_account="6000")
    assert rule.require_inspection is True
    assert rule.source == "vendor"
    # tolerance came from the commodity layer (vendor rule omitted it).
    assert rule.tolerance_pct == 1.5


def test_per_field_fallthrough_to_org_then_default():
    # Vendor rule sets only require_inspection; no commodity rule; tolerance
    # falls through to the org default.
    settings = {
        "matching": {
            "tolerance_pct": 7.5,
            "vendor_rules": {str(_VENDOR_ID): {"require_inspection": True}},
        }
    }
    rule = resolve_match_rule(settings, vendor_id=_VENDOR_ID, gl_account="6000")
    assert rule.require_inspection is True
    assert rule.tolerance_pct == 7.5


def test_vendor_id_as_uuid_and_str_resolve_identically():
    settings = {
        "matching": {
            "vendor_rules": {str(_VENDOR_ID): {"require_inspection": True, "tolerance_pct": 2.0}},
        }
    }
    by_uuid = resolve_match_rule(settings, vendor_id=_VENDOR_ID, gl_account=None)
    by_str = resolve_match_rule(settings, vendor_id=str(_VENDOR_ID), gl_account=None)
    assert by_uuid == by_str
    assert by_uuid.require_inspection is True
    assert by_uuid.tolerance_pct == 2.0


def test_none_vendor_and_gl_skip_rule_lookups():
    settings = {
        "matching": {
            "require_inspection": True,
            "tolerance_pct": 3.0,
            "vendor_rules": {str(_VENDOR_ID): {"require_inspection": False, "tolerance_pct": 9.0}},
            "commodity_rules": {"6000": {"require_inspection": False, "tolerance_pct": 8.0}},
        }
    }
    rule = resolve_match_rule(settings, vendor_id=None, gl_account=None)
    # Neither vendor nor commodity matched → org default.
    assert rule.require_inspection is True
    assert rule.tolerance_pct == 3.0
    assert rule.source == "org"


def test_malformed_rules_are_ignored():
    settings = {
        "matching": {
            "require_inspection": True,
            "tolerance_pct": 4.0,
            # vendor_rules is a list, not a dict → ignored.
            "vendor_rules": ["nope"],
            # commodity rule value is a string, not a dict → ignored.
            "commodity_rules": {"6000": "not-a-dict"},
        }
    }
    rule = resolve_match_rule(settings, vendor_id=_VENDOR_ID, gl_account="6000")
    assert rule.require_inspection is True
    assert rule.tolerance_pct == 4.0
    assert rule.source == "org"


def test_matching_not_a_dict_is_ignored():
    rule = resolve_match_rule({"matching": "broken"}, vendor_id=_VENDOR_ID, gl_account="6000")
    assert rule.require_inspection is False
    assert rule.tolerance_pct == 5.0
    assert rule.source == "default"


def test_non_numeric_tolerance_falls_through():
    settings = {
        "matching": {
            "tolerance_pct": 6.0,
            "vendor_rules": {str(_VENDOR_ID): {"tolerance_pct": "lots"}},
        }
    }
    rule = resolve_match_rule(settings, vendor_id=_VENDOR_ID, gl_account=None)
    # Bad vendor tolerance ignored → falls through to org default.
    assert rule.tolerance_pct == 6.0


def test_bool_tolerance_rejected():
    # A bool is an int subclass; must not be accepted as a numeric tolerance.
    settings = {"matching": {"tolerance_pct": True}}
    rule = resolve_match_rule(settings, vendor_id=None, gl_account=None)
    assert rule.tolerance_pct == DEFAULT_TOLERANCE_PCT


def test_int_tolerance_coerced_to_decimal():
    # Money/percent is exact Decimal end-to-end — the resolved tolerance flows
    # straight into the PO-match gate, which compares in Decimal. An int (or
    # float) config value is coerced to Decimal, never left as a binary float.
    settings = {"matching": {"tolerance_pct": 2}}
    rule = resolve_match_rule(settings, vendor_id=None, gl_account=None)
    assert rule.tolerance_pct == Decimal("2")
    assert isinstance(rule.tolerance_pct, Decimal)


def test_float_tolerance_bridged_through_str_not_binary_float():
    # A float config literal like 2.5 must land as Decimal('2.5'), not the
    # binary-float artefact Decimal(2.5) would produce.
    settings = {"matching": {"tolerance_pct": 2.5}}
    rule = resolve_match_rule(settings, vendor_id=None, gl_account=None)
    assert rule.tolerance_pct == Decimal("2.5")
    assert isinstance(rule.tolerance_pct, Decimal)


def test_string_tolerance_is_honoured_and_never_loosened_to_the_default():
    """An exact-decimal STRING is the project's own JSONB money representation.

    These rules live in a hand-edited settings blob and `match_invoice_to_po`
    already types its `tolerance_pct` `Decimal | float | int | str`. Rejecting
    `"1.0"` returned `None` — which means "fall through" — so the walk ended at
    `DEFAULT_TOLERANCE_PCT` (5.0), LOOSER than any tolerance an org would
    bother configuring. A supplier tightened to 1% silently got 5%: an invoice
    4.5% over its PO read `within_tolerance: True` and never raised a
    `po_mismatch`.
    """
    settings = {
        "matching": {
            "tolerance_pct": 6.0,
            "vendor_rules": {str(_VENDOR_ID): {"tolerance_pct": "1.0"}},
        }
    }
    rule = resolve_match_rule(settings, vendor_id=_VENDOR_ID, gl_account=None)
    assert rule.tolerance_pct == Decimal("1.0")
    assert isinstance(rule.tolerance_pct, Decimal)
    # Emphatically not the org value, and above all not the 5.0 default.
    assert rule.tolerance_pct != DEFAULT_TOLERANCE_PCT

    # ...at every layer of the walk, not just the vendor one.
    org_only = resolve_match_rule(
        {"matching": {"tolerance_pct": "2.50"}}, vendor_id=None, gl_account=None
    )
    assert org_only.tolerance_pct == Decimal("2.50")
    commodity = resolve_match_rule(
        {"matching": {"commodity_rules": {"6000": {"tolerance_pct": " 3 "}}}},
        vendor_id=None,
        gl_account="6000",
    )
    assert commodity.tolerance_pct == Decimal("3")


def test_non_finite_string_tolerance_falls_through():
    """`Decimal("NaN")` parses but cannot be compared against a variance —
    treat it as absent config rather than as a rule."""
    for junk in ("NaN", "Infinity", "-Infinity"):
        settings = {
            "matching": {
                "tolerance_pct": 6.0,
                "vendor_rules": {str(_VENDOR_ID): {"tolerance_pct": junk}},
            }
        }
        rule = resolve_match_rule(settings, vendor_id=_VENDOR_ID, gl_account=None)
        assert rule.tolerance_pct == Decimal("6.0"), junk


def test_commodity_inspection_false_overrides_org_true():
    # An explicit False at the commodity layer must win over an org-level True
    # (per-field "first present value", not "first truthy").
    settings = {
        "matching": {
            "require_inspection": True,
            "commodity_rules": {"6000": {"require_inspection": False}},
        }
    }
    rule = resolve_match_rule(settings, vendor_id=None, gl_account="6000")
    assert rule.require_inspection is False
    assert rule.source == "commodity"
