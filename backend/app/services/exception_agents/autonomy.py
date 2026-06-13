from __future__ import annotations

from decimal import Decimal

# autonomy_level → minimum confidence required to AUTO-RESOLVE.
# conservative is "off": threshold > 1.0 means nothing ever clears it, so
# every exception escalates (the safe default).
_THRESHOLDS: dict[str, Decimal] = {
    "conservative": Decimal("1.01"),  # unreachable — escalate everything
    "balanced": Decimal("0.90"),
    "aggressive": Decimal("0.75"),
}

_DEFAULT_LEVEL = "conservative"


def resolve_autonomy_level(org_settings: dict | None) -> str:
    """Read Organization.settings.exception_agents.autonomy_level with a safe
    default. Unknown values fall back to conservative (fail-closed)."""
    cfg = (org_settings or {}).get("exception_agents") or {}
    level = cfg.get("autonomy_level", _DEFAULT_LEVEL)
    return level if level in _THRESHOLDS else _DEFAULT_LEVEL


def autonomy_threshold(level: str) -> Decimal:
    return _THRESHOLDS.get(level, _THRESHOLDS[_DEFAULT_LEVEL])
