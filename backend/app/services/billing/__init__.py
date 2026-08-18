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
from app.services.billing.period import BillingPeriod, current_period
from app.services.billing.plan_change import (
    PlanChangeError,
    PlanChangeResult,
    change_plan,
)
from app.services.billing.proration import ProrationResult, compute_proration
from app.services.billing.provisioning import (
    ProvisionedIds,
    provision_org_billing,
)
from app.services.billing.usage_rollup import UsageRollup, rollup_usage

__all__ = [
    "BillingPeriod",
    "current_period",
    "UsageRollup",
    "rollup_usage",
    "get_active_subscription",
    "get_entitlements",
    "has_entitlement",
    "compute_proration",
    "ProrationResult",
    "provision_org_billing",
    "ProvisionedIds",
    "change_plan",
    "PlanChangeError",
    "PlanChangeResult",
]
