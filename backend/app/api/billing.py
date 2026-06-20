"""Customer-facing billing — current plan, status, usage-to-date (`/api/billing`).

FIRST SLICE: a single read endpoint, ``GET /api/billing/subscription``. It
returns the requesting tenant's live plan + subscription status + a usage rollup
for the current period. Plan-change, payment-method, and invoice-list endpoints
are later slices.

Auth-before-everything: behind JWT + ``require_roles(admin, cfo)`` (billing is a
finance/admin concern). The org is resolved from the tenant chokepoint
(``get_tenant``), and the usage rollup reads the CONTROL-PLANE usage tables off
``get_control_db``. Money is serialised as exact decimal strings (never float) —
this is a billing surface where exactness is the point. See
``backend/docs/billing.md``.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ROLE_ADMIN, ROLE_CFO, require_roles
from app.config import settings
from app.database import get_control_db
from app.models.organization import Organization
from app.models.user import User
from app.services.billing import get_active_subscription, rollup_usage
from app.tenant import get_tenant, get_tenant_db

router = APIRouter(prefix="/billing", tags=["billing"])


class PlanView(BaseModel):
    code: str
    name: str
    # Exact money as a decimal string — never float on a billing surface.
    monthly_price: str
    currency: str
    entitlements: dict
    trial_days: int


class SubscriptionView(BaseModel):
    status: str
    current_period_start: datetime | None
    current_period_end: datetime | None
    trial_end: datetime | None
    # Whether a live provider subscription backs this (always False on mock).
    externally_managed: bool


class BillingSummaryResponse(BaseModel):
    # Provider in effect for this org (per-org override → AP_BILLING_PROVIDER).
    provider: str
    # None when the org has no live subscription (e.g. never subscribed).
    plan: PlanView | None
    subscription: SubscriptionView | None
    period: str
    # Billable meters for the current period, as exact decimal strings.
    usage: dict[str, str]


def _current_period() -> str:
    return datetime.now(UTC).strftime("%Y-%m")


def _resolve_provider(org: Organization) -> str:
    """Per-org override (`settings.billing.provider`) → platform default."""
    billing = (org.settings or {}).get("billing") or {}
    return billing.get("provider") or settings.billing_provider


@router.get("/subscription", response_model=BillingSummaryResponse)
async def get_subscription(
    org: Organization = Depends(get_tenant),
    _user: User = Depends(require_roles(ROLE_ADMIN, ROLE_CFO)),
    control_db: AsyncSession = Depends(get_control_db),
    tenant_db: AsyncSession = Depends(get_tenant_db),
) -> BillingSummaryResponse:
    """Current plan + subscription status + usage-to-date for the tenant.

    Plans/subscriptions are control-plane (`control_db`); the usage meters
    (`extraction_usage` / `card_rebates`) are tenant-scoped, so the rollup reads
    `tenant_db`.
    """
    period = _current_period()
    active = await get_active_subscription(control_db, org.id)

    plan_view: PlanView | None = None
    sub_view: SubscriptionView | None = None
    if active is not None:
        subscription, plan = active
        plan_view = PlanView(
            code=plan.code,
            name=plan.name,
            monthly_price=str(plan.monthly_price),
            currency=plan.currency,
            entitlements=dict(plan.entitlements or {}),
            trial_days=plan.trial_days,
        )
        sub_view = SubscriptionView(
            status=subscription.status,
            current_period_start=subscription.current_period_start,
            current_period_end=subscription.current_period_end,
            trial_end=subscription.trial_end,
            externally_managed=subscription.external_subscription_id is not None,
        )

    usage = await rollup_usage(tenant_db, organization_id=org.id, period=period)

    return BillingSummaryResponse(
        provider=_resolve_provider(org),
        plan=plan_view,
        subscription=sub_view,
        period=period,
        usage=usage.as_meters(),
    )
