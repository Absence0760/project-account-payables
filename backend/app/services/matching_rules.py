"""Resolve the effective PO-match rule for an invoice.

PO matching has two tunable knobs: ``require_inspection`` (does a matched PO
need a quality-inspection record before the invoice clears the 4-way leg) and
``tolerance_pct`` (the allowed amount variance vs. the PO). Historically both
came from a single per-org setting. This module lets an org configure either
knob *per vendor* or *per commodity type*, falling back to the org default.

"Commodity type" is the invoice's header GL account (``invoice.gl_account``, a
``String(100)`` column that already exists) — no new DB columns.

The settings shape (all keys optional, backward-compatible) lives under
``Organization.settings.matching``::

    {
      "matching": {
        "require_inspection": false,
        "tolerance_pct": 5.0,
        "vendor_rules":    { "<vendor_id_uuid_str>": { "require_inspection": true,
                                                       "tolerance_pct": 2.0 } },
        "commodity_rules": { "<gl_account_code>":     { "require_inspection": true,
                                                       "tolerance_pct": 1.0 } }
      }
    }

Precedence is **per-field** (not whole-rule): for each of ``require_inspection``
and ``tolerance_pct`` independently, take the first present value walking

    vendor_rules[str(vendor_id)]  →  commodity_rules[gl_account]  →
    matching.<field>  →  hardcoded default (require_inspection=False, tolerance_pct=5.0)

So a vendor rule that only sets ``require_inspection`` still lets
``tolerance_pct`` fall through to the commodity / org / default layers.

The resolver is pure (no DB, no I/O) and never raises: malformed config —
non-dict rules, missing keys, ``vendor_id``/``gl_account`` ``None``, non-numeric
tolerance — is silently ignored in favour of the next layer down.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

DEFAULT_REQUIRE_INSPECTION = False
# Decimal, never float — this tolerance flows straight into the PO-match gate,
# which compares in exact Decimal. Keeping the whole path Decimal means a
# per-vendor tolerance like 2.5 never picks up a binary-float artefact.
DEFAULT_TOLERANCE_PCT = Decimal("5.0")


@dataclass(frozen=True)
class EffectiveMatchRule:
    """The resolved per-invoice match rule.

    ``source`` records where ``require_inspection`` was resolved from
    ("vendor" | "commodity" | "org" | "default") for debuggability / logging.
    """

    require_inspection: bool
    tolerance_pct: Decimal
    source: str


def _rule_for(rules: object, key: str | None) -> dict:
    """Return the rule dict for ``key`` within ``rules``, or ``{}`` if absent/malformed."""
    if key is None or not isinstance(rules, dict):
        return {}
    rule = rules.get(key)
    return rule if isinstance(rule, dict) else {}


def _coerce_inspection(rule: dict) -> bool | None:
    """Extract ``require_inspection`` from a rule dict, or ``None`` if absent."""
    if "require_inspection" not in rule:
        return None
    return bool(rule["require_inspection"])


def _coerce_tolerance(rule: dict) -> Decimal | None:
    """Extract a numeric ``tolerance_pct`` as exact Decimal, or ``None`` if
    absent/non-numeric. Floats bridge through ``str`` so ``2.5`` lands as
    ``Decimal('2.5')``, never the binary-float artefact ``Decimal(2.5)`` gives."""
    if "tolerance_pct" not in rule:
        return None
    value = rule["tolerance_pct"]
    # Reject bools (a bool is an int subclass) and anything non-numeric.
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def resolve_match_rule(
    org_settings: dict | None,
    *,
    vendor_id: uuid.UUID | str | None,
    gl_account: str | None,
) -> EffectiveMatchRule:
    """Resolve the effective match rule for one invoice.

    Per-field precedence: vendor rule → commodity rule → org default →
    hardcoded default. ``vendor_id`` may be a ``uuid.UUID``, ``str``, or
    ``None``; commodity is the invoice's header GL account (``gl_account``).
    Never raises — malformed config falls through to the next layer.
    """
    matching = (org_settings or {}).get("matching")
    if not isinstance(matching, dict):
        matching = {}

    vendor_key = str(vendor_id) if vendor_id is not None else None
    vendor_rule = _rule_for(matching.get("vendor_rules"), vendor_key)
    commodity_rule = _rule_for(matching.get("commodity_rules"), gl_account)
    org_inspection = _coerce_inspection(matching)
    org_tolerance = _coerce_tolerance(matching)

    # require_inspection — per-field walk, tracking the source layer.
    require_inspection = DEFAULT_REQUIRE_INSPECTION
    source = "default"
    if (value := _coerce_inspection(vendor_rule)) is not None:
        require_inspection, source = value, "vendor"
    elif (value := _coerce_inspection(commodity_rule)) is not None:
        require_inspection, source = value, "commodity"
    elif org_inspection is not None:
        require_inspection, source = org_inspection, "org"

    # tolerance_pct — independent per-field walk.
    tolerance_pct = DEFAULT_TOLERANCE_PCT
    if (value := _coerce_tolerance(vendor_rule)) is not None:
        tolerance_pct = value
    elif (value := _coerce_tolerance(commodity_rule)) is not None:
        tolerance_pct = value
    elif org_tolerance is not None:
        tolerance_pct = org_tolerance

    return EffectiveMatchRule(
        require_inspection=require_inspection,
        tolerance_pct=tolerance_pct,
        source=source,
    )
