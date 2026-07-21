"""Mid-period plan change — orchestrates proration + provisioning + audit.

Changing the plan behind an org's live ``Subscription`` part-way through a
billing period:

  1. Resolve the org's live subscription + current plan **row-locked**
     (`_get_active_subscription_for_update`, ``SELECT ... FOR UPDATE``). A
     second concurrent ``change_plan`` call for the same org blocks behind
     this one's commit and then re-reads the already-updated subscription as
     its own baseline, instead of racing off the same stale "current" plan
     (see `docs/billing.md` § Concurrency).
  2. **Idempotent no-op** when the target plan equals the current plan — returns
     a zero proration, mutates nothing, writes no audit row (mirrors
     `transition_invoice` / `apply_billing_event`'s no-op rule). A retry of the
     same change therefore can't double-charge.
  3. Compute the prorated adjustment (`compute_proration`, pure Decimal) off the
     locked baseline.
  4. Resolve-or-create the provider customer + the NEW plan's price
     (`provision_org_billing`) so the live adapter has what it needs.
  5. Repoint the subscription at the new plan, persist, and write an append-only
     ``billing.plan_changed`` audit row (PII-free; old/new plan + proration as an
     exact decimal string).

Money-path boundary
-------------------
This NEVER moves money directly. The proration is computed and recorded; the
actual charge/credit is the provider's job (a live ``stripe_billing`` would issue
the proration line on the next invoice). The ``mock`` provider no-ops, so locally
the proration is informational. Idempotency is the "already on this plan" guard
plus the provider's own idempotency keys in `provision_org_billing`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing import Plan, Subscription
from app.models.organization import Organization
from app.services.audit_dispatch import dispatch_auth_audit
from app.services.billing.proration import ProrationResult, compute_proration
from app.services.billing.provisioning import provision_org_billing

logger = logging.getLogger(__name__)


async def _get_active_subscription_for_update(
    db: AsyncSession, organization_id
) -> tuple[Subscription, Plan] | None:
    """Row-locked variant of ``entitlements.get_active_subscription``, for
    ``change_plan``'s read-modify-write ONLY.

    Plain read-only callers (the entitlement-check dependencies, the
    subscription-summary endpoint) keep using the unlocked
    ``get_active_subscription`` — they never mutate, so taking a row lock
    there would only add contention with no correctness benefit. This variant
    exists so a second concurrent plan change for the same org blocks on the
    ``FOR UPDATE`` until the first commits, then observes the first change's
    result as its own "current" baseline instead of a stale pre-change read.

    Deliberately **two separate queries**, not one ``SELECT ... FOR UPDATE``
    join across ``Subscription`` and ``Plan``: Postgres's lock-wait recheck
    (EvalPlanQual) re-fetches the latest version of the LOCKED row but reuses
    the join partner from the ORIGINAL scan. Since a plan change rewrites
    ``Subscription.plan_id`` — the very column the join keys on — a second
    waiter's joined query would recheck the new ``plan_id`` against the OLD
    (pre-change) ``Plan`` row, the join predicate would fail, and the query
    would come back with no row at all, even though the subscription plainly
    exists (confirmed with a two-connection repro against a real Postgres:
    the joined-``FOR UPDATE`` form drops the row after the first racer
    commits; splitting the lock from the ``Plan`` lookup does not). Locking
    only ``Subscription`` and then issuing a fresh, unlocked ``Plan`` lookup
    keyed off the just-locked (and therefore current) ``plan_id`` sidesteps
    the pitfall entirely.
    """
    subscription = (
        await db.execute(
            select(Subscription)
            .where(
                Subscription.organization_id == organization_id,
                Subscription.status != "canceled",
            )
            .order_by(Subscription.created_at.desc())
            .limit(1)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if subscription is None:
        return None
    plan = (
        await db.execute(select(Plan).where(Plan.id == subscription.plan_id))
    ).scalar_one_or_none()
    if plan is None:
        return None
    return subscription, plan


class PlanChangeError(RuntimeError):
    """Raised for a caller-correctable plan-change failure (no live sub, bad plan)."""


@dataclass(frozen=True)
class PlanChangeResult:
    changed: bool
    old_plan_code: str
    new_plan_code: str
    proration: ProrationResult
    reason: str | None = None


async def change_plan(
    control_db: AsyncSession,
    *,
    org: Organization,
    new_plan_code: str,
    actor_id,
    change_at: datetime | None = None,
) -> PlanChangeResult:
    """Move the org's live subscription to ``new_plan_code``, prorated.

    Raises :class:`PlanChangeError` (the API maps to 4xx) when the org has no
    live subscription or the target plan code is unknown/inactive. A change to
    the plan the org is already on is a successful **no-op** (``changed=False``).
    """
    now = change_at or datetime.now(UTC)

    active = await _get_active_subscription_for_update(control_db, org.id)
    if active is None:
        raise PlanChangeError("no_live_subscription")
    subscription, current_plan = active

    new_plan = (
        await control_db.execute(
            select(Plan).where(Plan.code == new_plan_code, Plan.is_active.is_(True))
        )
    ).scalar_one_or_none()
    if new_plan is None:
        raise PlanChangeError("unknown_plan")

    # Idempotent no-op: already on the target plan. No mutation, no audit row, no
    # provider call — a retry of the same change can't double-charge.
    if current_plan.id == new_plan.id:
        return PlanChangeResult(
            changed=False,
            old_plan_code=current_plan.code,
            new_plan_code=new_plan.code,
            proration=_zero_proration(current_plan.monthly_price, new_plan.monthly_price),
            reason="already_on_plan",
        )

    proration = compute_proration(
        old_monthly=current_plan.monthly_price,
        new_monthly=new_plan.monthly_price,
        period_start=subscription.current_period_start or now,
        period_end=subscription.current_period_end or now,
        change_at=now,
    )

    # Resolve-or-create the provider customer + the NEW plan's price so the live
    # adapter has what create/update needs. Fails closed (BillingNotConfigured)
    # with the live adapter and no key — before we mutate the subscription.
    await provision_org_billing(control_db, org=org, plan=new_plan)

    # Guard the (org, plan) unique constraint: a leftover *canceled* row for the
    # target plan would collide when we repoint plan_id. Drop the stale canceled
    # row first (history of a plan the org is re-adopting; the live row is the
    # source of truth) so the repoint is safe.
    await control_db.execute(
        Subscription.__table__.delete().where(
            Subscription.organization_id == org.id,
            Subscription.plan_id == new_plan.id,
            Subscription.status == "canceled",
        )
    )

    # Repoint the subscription. The provider-side amendment (issuing the
    # proration line) is the live adapter's job on its next invoice cycle; the
    # mock provider no-ops. We record the intent + amount immutably here.
    previous_plan_code = current_plan.code
    subscription.plan_id = new_plan.id

    # Append-only audit (SOX): plan change is a regulated control-plane mutation.
    # PII-free — org, old/new plan code, proration as an exact decimal STRING.
    # Dispatched BEFORE the commit (mirrors apply_billing_event) so the change is
    # never durably persisted without an audit attempt; dispatch_auth_audit is
    # fail-soft and opens its own tenant session.
    await dispatch_auth_audit(
        organization_id=org.id,
        actor_id=actor_id,
        action="billing.plan_changed",
        entity_id=subscription.id,
        details={
            "from_plan": previous_plan_code,
            "to_plan": new_plan.code,
            "proration_amount": str(proration.amount),
            "unused_days": proration.unused_days,
            "period_days": proration.period_days,
        },
    )
    await control_db.commit()

    return PlanChangeResult(
        changed=True,
        old_plan_code=previous_plan_code,
        new_plan_code=new_plan.code,
        proration=proration,
    )


def _zero_proration(old_monthly: Decimal, new_monthly: Decimal) -> ProrationResult:
    return ProrationResult(
        amount=Decimal("0.00"),
        unused_days=0,
        period_days=0,
        old_monthly=old_monthly,
        new_monthly=new_monthly,
    )
