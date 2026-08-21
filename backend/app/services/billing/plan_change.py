"""Mid-period plan change — orchestrates proration + provisioning + audit.

Changing the plan behind an org's live ``Subscription`` part-way through a
billing period:

  1. **Pre-flight, unlocked.** Resolve the target plan, confirm the org has a
     live subscription at all, and short-circuit a same-plan retry (so it never
     reaches the provider). Then resolve-or-create the provider customer + the
     NEW plan's price (`provision_org_billing`).
  2. Resolve the org's live subscription + current plan **row-locked**
     (`_get_active_subscription_for_update`, ``SELECT ... FOR UPDATE``). A
     second concurrent ``change_plan`` call for the same org blocks behind
     this one's commit and then re-reads the already-updated subscription as
     its own baseline, instead of racing off the same stale "current" plan
     (see `docs/billing.md` § Concurrency).
  3. **Idempotent no-op** when the target plan equals the current plan — returns
     a zero proration, mutates nothing, writes no audit row (mirrors
     `transition_invoice` / `apply_billing_event`'s no-op rule). A retry of the
     same change therefore can't double-charge. Re-checked here under the lock,
     because a racer may have moved the org onto this plan since the peek.
  4. Resolve (and persist) the subscription's current billing window via
     `period.current_period`, then compute the prorated adjustment
     (`compute_proration`, pure Decimal) off the locked baseline. Nothing used
     to write `current_period_start`/`_end`, so this step used to divide by a
     zero-length window and prorate `0.00` on every change.
  5. Repoint the subscription at the new plan, persist, and write an append-only
     ``billing.plan_changed`` audit row (PII-free; old/new plan + proration as an
     exact decimal string).

**Step 1 must stay ahead of step 2.** `provision_org_billing` commits, and a
commit inside the locked section RELEASES the row lock mid-transaction: the
waiting racer's ``FOR UPDATE`` then returned the still-unrepointed row, both
changes prorated off the same stale plan, and both wrote an audit row claiming
the same ``from_plan``. That happened whenever provisioning had anything to
persist — i.e. on the first change to any plan the org had no stored price id
for. Keeping it ahead of the lock also keeps the (live, third-party) Stripe
round-trip out of the locked window.

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
from app.services.billing.entitlements import get_active_subscription
from app.services.billing.period import current_period
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

    ``populate_existing=True`` is load-bearing, not defensive tidiness. If this
    session already holds the ``Subscription`` in its identity map — which it
    does, because ``change_plan``'s unlocked pre-flight peek loaded it — the
    default behaviour hands that instance BACK with its previously-loaded
    column values and discards the ones the locked SELECT just read. A second
    racer would then unblock, re-read the row Postgres has since updated, and
    still see the stale ``plan_id``, defeating the lock entirely. Same reason
    ``api/webhooks._get_owned_subscription`` sets it before a rotation.
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
            .execution_options(populate_existing=True)
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

    new_plan = (
        await control_db.execute(
            select(Plan).where(Plan.code == new_plan_code, Plan.is_active.is_(True))
        )
    ).scalar_one_or_none()
    if new_plan is None:
        raise PlanChangeError("unknown_plan")

    # --- Pre-flight, deliberately BEFORE the row lock ------------------------
    # `provision_org_billing` COMMITS (it persists the resolved provider ids onto
    # `Organization.settings.billing`). Calling it between the `FOR UPDATE` read
    # and the repoint released that lock half-way through the read-modify-write:
    # the second racer's blocked SELECT then returned the still-unrepointed row,
    # so BOTH changes prorated off the same stale baseline and both wrote an
    # audit row claiming the same `from_plan` — the exact lost update
    # `_get_active_subscription_for_update` exists to prevent. It fired whenever
    # provisioning had anything to persist, i.e. on the FIRST change to any plan
    # the org has no stored price id for — the common case, not an edge one.
    #
    # Provisioning here also keeps the provider round-trip (up to two live
    # Stripe calls) OUT of the locked window, so an inbound billing webhook or
    # the dunning sweep can't be parked behind it on the same subscription row.
    #
    # The unlocked peek is what preserves "a same-plan retry makes no provider
    # call"; the AUTHORITATIVE no-live-subscription / already-on-plan checks are
    # still the locked ones below.
    peek = await get_active_subscription(control_db, org.id)
    if peek is None:
        raise PlanChangeError("no_live_subscription")
    _peek_subscription, peek_plan = peek
    if peek_plan.id == new_plan.id:
        return PlanChangeResult(
            changed=False,
            old_plan_code=peek_plan.code,
            new_plan_code=new_plan.code,
            proration=_zero_proration(peek_plan.monthly_price, new_plan.monthly_price),
            reason="already_on_plan",
        )

    # Resolve-or-create the provider customer + the NEW plan's price so the live
    # adapter has what create/update needs. Fails closed (BillingNotConfigured)
    # with the live adapter and no key — before anything is locked or mutated.
    await provision_org_billing(control_db, org=org, plan=new_plan)

    # --- Locked read-modify-write: ONE transaction, ONE commit ---------------
    active = await _get_active_subscription_for_update(control_db, org.id)
    if active is None:
        raise PlanChangeError("no_live_subscription")
    subscription, current_plan = active

    # Idempotent no-op: already on the target plan. No mutation, no audit row —
    # a retry of the same change can't double-charge. Re-checked under the lock
    # because a racer may have moved the org onto this plan since the peek.
    if current_plan.id == new_plan.id:
        return PlanChangeResult(
            changed=False,
            old_plan_code=current_plan.code,
            new_plan_code=new_plan.code,
            proration=_zero_proration(current_plan.monthly_price, new_plan.monthly_price),
            reason="already_on_plan",
        )

    # Resolve the window the subscription is actually in, and PERSIST it. The
    # old `subscription.current_period_start or now` / `... or now` fallback
    # handed `compute_proration` a zero-length window on every real
    # subscription — nothing ever wrote those columns — so its
    # degenerate-window guard short-circuited and every plan change prorated
    # `0.00` while reporting that figure as correct. `current_period` is the
    # one rule the summary endpoint and the dunning grace clock read too, so
    # writing the resolved window back here also un-sticks a row those two
    # were reading as NULL. See `services/billing/period.py`.
    period = current_period(subscription, now=now)
    subscription.current_period_start = period.start
    subscription.current_period_end = period.end

    proration = compute_proration(
        old_monthly=current_plan.monthly_price,
        new_monthly=new_plan.monthly_price,
        period_start=period.start,
        period_end=period.end,
        change_at=now,
    )

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
