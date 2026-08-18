"""Default plan catalog + baseline Subscription provisioning (control plane).

Before this module, nothing in the app ever created a `Plan` or `Subscription`
row outside of tests (every test exercising `/api/v1` or `/api/billing` had to
hand-seed both). Two consequences: `require_entitlement` /
`require_api_entitlement` fail closed to `{}` for every org (issue #180 — the
public Developer API 402s for every org, forever, with no way out), and
`services/billing/plan_change.py::change_plan` 404s with "no live subscription"
for every org, so an admin could never even upgrade out of it.

`ensure_plan_catalog` + `ensure_subscription` fix this at the two places a
tenant comes into being: `services/tenant_provisioning.py` (CLI + self-service
signup) and `scripts/seed.py` (the demo tenants). Both are idempotent — safe to
call on every provision, never duplicates a plan by `code` or creates a second
live subscription for an org.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing import Plan, Subscription
from app.services.billing.period import add_months

# Stable machine codes referenced throughout backend/docs/billing.md (its
# `GET /api/billing/subscription` example uses exactly this "growth" /
# $49.00 / 14-trial-day shape). `free` is the default new tenants land on;
# `public_api` (the only entitlement gated in the app today) is a paid-plan
# feature, so it's withheld on `free` by design — an org upgrades via
# `POST /api/billing/change-plan` to unlock it.
DEFAULT_PLAN_CATALOG: tuple[dict, ...] = (
    {
        "code": "free",
        "name": "Free",
        "monthly_price": Decimal("0.00"),
        "entitlements": {},
        "trial_days": 0,
    },
    {
        "code": "growth",
        "name": "Growth",
        "monthly_price": Decimal("49.00"),
        "entitlements": {"public_api": True},
        "trial_days": 14,
    },
    {
        "code": "scale",
        "name": "Scale",
        "monthly_price": Decimal("199.00"),
        "entitlements": {"public_api": True},
        "trial_days": 14,
    },
)


async def ensure_plan_catalog(session: AsyncSession) -> dict[str, Plan]:
    """Idempotently create any :data:`DEFAULT_PLAN_CATALOG` entry missing by
    `code`. Never touches a plan that already exists — an operator who has
    since edited a plan's price/entitlements keeps their edits; this only
    fills in gaps on a fresh control DB. Returns every catalog plan (existing
    + newly created), keyed by code, so the caller can bind a Subscription.
    """
    codes = [spec["code"] for spec in DEFAULT_PLAN_CATALOG]
    existing = (await session.execute(select(Plan).where(Plan.code.in_(codes)))).scalars().all()
    by_code = {p.code: p for p in existing}

    for spec in DEFAULT_PLAN_CATALOG:
        if spec["code"] in by_code:
            continue
        plan = Plan(
            id=uuid.uuid4(),
            code=spec["code"],
            name=spec["name"],
            monthly_price=spec["monthly_price"],
            currency="USD",
            entitlements=spec["entitlements"],
            trial_days=spec["trial_days"],
        )
        session.add(plan)
        by_code[spec["code"]] = plan

    await session.flush()
    return by_code


async def ensure_subscription(
    session: AsyncSession, *, organization_id: uuid.UUID, plan_code: str
) -> Subscription | None:
    """Bind `organization_id` to `plan_code` if it has no live subscription
    yet (mirrors `uq_subscription_one_live_per_org`: at most one row with
    `status != "canceled"`). No-ops and returns the existing row if the org
    is already subscribed to anything — never creates a second live
    subscription. Returns `None` when `plan_code` isn't in the catalog yet
    (a fresh control DB before `ensure_plan_catalog` has run) — mirrors the
    "skip silently, don't crash provisioning" pattern already used for the
    admin role lookup in `tenant_provisioning._provision_into`.
    """
    existing = (
        await session.execute(
            select(Subscription).where(
                Subscription.organization_id == organization_id,
                Subscription.status != "canceled",
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    plan = (await session.execute(select(Plan).where(Plan.code == plan_code))).scalar_one_or_none()
    if plan is None:
        return None

    # Stamp the first billing window. Plans are flat monthly, and every reader
    # of these columns (proration, the dunning grace clock, the subscription
    # summary) is useless without them — leaving them NULL is what made every
    # mid-period plan change prorate 0.00. See `services/billing/period.py`.
    started = datetime.now(UTC)
    sub = Subscription(
        id=uuid.uuid4(),
        organization_id=organization_id,
        plan_id=plan.id,
        status="active",
        current_period_start=started,
        current_period_end=add_months(started, 1),
    )
    session.add(sub)
    await session.flush()
    return sub
