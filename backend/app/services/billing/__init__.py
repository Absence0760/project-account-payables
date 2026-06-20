"""Platform billing & metering services.

``usage_rollup`` aggregates control-plane usage tables (``extraction_usage``,
``card_rebates``) into billable meters per org per period. Pure / queryable,
``Decimal``-exact, no mutation. See ``backend/docs/billing.md``.
"""

from app.services.billing.entitlements import (
    get_active_subscription,
    get_entitlements,
    has_entitlement,
)
from app.services.billing.usage_rollup import UsageRollup, rollup_usage

__all__ = [
    "UsageRollup",
    "rollup_usage",
    "get_active_subscription",
    "get_entitlements",
    "has_entitlement",
]
