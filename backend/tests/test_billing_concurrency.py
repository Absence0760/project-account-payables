"""Real-Postgres concurrency test for the billing plan-change read-modify-write.

Proves the row-lock fix for a confirmed lost-update race that a mocked-session
suite structurally cannot catch (a single ``MagicMock`` session can't model two
connections contending for a row lock). Mirrors ``tests/test_payment_concurrency.py``:
the ``realdb`` fixture's per-key session makers hand back independent
engines/connections, so two coroutines run against genuinely separate DB
sessions and Postgres's ``SELECT ... FOR UPDATE`` actually serializes them.

BUG — ``change_plan`` used to resolve the org's live subscription via a plain
``SELECT`` (no ``FOR UPDATE``), then prorate and repoint off that read. Two
concurrent *different* plan changes for the same org (A→B and A→C) both read
``current = A``, both prorate off A's price, and both then repoint — the loser
computes its proration against a now-stale baseline (the winner already moved
the subscription off A) and both write a ``billing.plan_changed`` audit row
claiming ``from_plan=A``, even though only one of them is still true once the
other has landed.

FIX — ``_get_active_subscription_for_update`` locks the subscription row
before proration. The second call now blocks behind the first's commit, then
re-reads the *already-updated* subscription as its own baseline: its
``from_plan`` and proration reflect the actual prior state at the time it
acquired the lock, not the value both calls originally read.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm.attributes import flag_modified

from app.models.billing import Plan, Subscription
from app.models.organization import Organization
from app.models.workflow import AuditLog
from app.services.billing.plan_change import change_plan
from app.services.billing.proration import compute_proration

pytestmark = pytest.mark.asyncio

PERIOD_START = datetime(2026, 6, 1, tzinfo=UTC)
PERIOD_END = datetime(2026, 7, 1, tzinfo=UTC)
CHANGE_AT = datetime(2026, 6, 16, tzinfo=UTC)  # 15 unused / 30 period days

PLAN_A = ("conctest_a", Decimal("40.00"))
PLAN_B = ("conctest_b", Decimal("100.00"))
PLAN_C = ("conctest_c", Decimal("220.00"))


async def _seed_plan(realdb, *, code: str, price: Decimal) -> uuid.UUID:
    plan_id = uuid.uuid4()
    async with realdb.control_sessionmaker()() as s:
        s.add(
            Plan(
                id=plan_id,
                code=code,
                name=code.title(),
                monthly_price=price,
                currency="USD",
                entitlements={},
                trial_days=0,
            )
        )
        await s.commit()
    return plan_id


async def _seed_subscription(realdb, *, org_id, plan_id) -> uuid.UUID:
    sub_id = uuid.uuid4()
    async with realdb.control_sessionmaker()() as s:
        s.add(
            Subscription(
                id=sub_id,
                organization_id=org_id,
                plan_id=plan_id,
                status="active",
                current_period_start=PERIOD_START,
                current_period_end=PERIOD_END,
            )
        )
        await s.commit()
    return sub_id


async def _preprovision_settings(realdb, org_id, *, price_ids: dict[str, str]) -> None:
    """Pre-populate settings.billing with a customer id + every target plan's
    price id, so ``provision_org_billing`` is a total no-op (no mutation, no
    intermediate commit) inside ``change_plan``. This isolates the test to the
    specific bug the issue describes — the unlocked read of the Subscription
    row — rather than the (separate, orthogonal) provisioning commit path."""
    async with realdb.control_sessionmaker()() as s:
        org = (await s.execute(select(Organization).where(Organization.id == org_id))).scalar_one()
        settings_dict = dict(org.settings or {})
        settings_dict["billing"] = {
            "stripe_customer_id": f"mock_cus_{org_id}",
            "plan_price_ids": price_ids,
        }
        org.settings = settings_dict
        flag_modified(org, "settings")
        await s.commit()


async def _cleanup(realdb, org_id) -> None:
    async with realdb.control_sessionmaker()() as s:
        await s.execute(delete(Subscription).where(Subscription.organization_id == org_id))
        await s.execute(delete(Plan).where(Plan.code.like("conctest_%")))
        org = (await s.execute(select(Organization).where(Organization.id == org_id))).scalar_one()
        settings_dict = dict(org.settings or {})
        settings_dict.pop("billing", None)
        org.settings = settings_dict
        flag_modified(org, "settings")
        await s.commit()
    # audit_log is append-only (SOX immutability trigger) — the two
    # billing.plan_changed rows this test writes are left in place, like
    # every other realdb test's audit trail.


async def _change(realdb, org_id, *, target_code: str, actor_id):
    """Run ``change_plan`` against a fresh session — its own DB connection, so
    two of these run truly concurrently against separate Postgres backends."""
    mk = realdb.control_sessionmaker()
    async with mk() as s:
        org = (await s.execute(select(Organization).where(Organization.id == org_id))).scalar_one()
        return await change_plan(
            s, org=org, new_plan_code=target_code, actor_id=actor_id, change_at=CHANGE_AT
        )


async def test_concurrent_plan_changes_serialize_not_lost_update(realdb):
    """Two concurrent change_plan calls (A->B and A->C) on the same org must
    serialize through the subscription row lock: exactly one ends up as the
    true prior state for the other, never both racing off the stale original
    plan. This is the exact scenario from issue #187."""
    org_id = realdb.info("a").org_id
    actor_id = realdb.info("a").users["admin"]
    try:
        plan_a_id = await _seed_plan(realdb, code=PLAN_A[0], price=PLAN_A[1])
        await _seed_plan(realdb, code=PLAN_B[0], price=PLAN_B[1])
        await _seed_plan(realdb, code=PLAN_C[0], price=PLAN_C[1])
        sub_id = await _seed_subscription(realdb, org_id=org_id, plan_id=plan_a_id)
        await _preprovision_settings(
            realdb,
            org_id,
            price_ids={
                PLAN_A[0]: "mock_price_a",
                PLAN_B[0]: "mock_price_b",
                PLAN_C[0]: "mock_price_c",
            },
        )

        res_b, res_c = await asyncio.gather(
            _change(realdb, org_id, target_code=PLAN_B[0], actor_id=actor_id),
            _change(realdb, org_id, target_code=PLAN_C[0], actor_id=actor_id),
        )

        # Both changes must have actually applied (this isn't a conflict/409
        # race like the payment-run one — both are legitimate plan changes
        # that must SERIALIZE, not corrupt each other).
        assert res_b.changed is True
        assert res_c.changed is True

        # Exactly one of the two racers observed the true original baseline
        # (plan A). The other — whichever acquired the row lock second —
        # must observe the FIRST racer's already-applied change as its own
        # "current" plan, never the stale original A both would have read
        # under the old unlocked code.
        if res_b.old_plan_code == PLAN_A[0]:
            first, second = res_b, res_c
        else:
            first, second = res_c, res_b

        assert first.old_plan_code == PLAN_A[0], (
            f"expected exactly one racer to baseline off the original plan, got "
            f"b.old={res_b.old_plan_code} c.old={res_c.old_plan_code}"
        )
        assert second.old_plan_code == first.new_plan_code, (
            "the second racer's baseline must be the first racer's already-applied "
            f"plan ({first.new_plan_code}), not the stale original — got "
            f"{second.old_plan_code} (this is the lost-update bug from #187)"
        )

        # The second racer's proration must be computed against the ACTUAL
        # prior state at the time it acquired the lock (first's new plan's
        # price), not the original plan A's price.
        price_by_code = {PLAN_A[0]: PLAN_A[1], PLAN_B[0]: PLAN_B[1], PLAN_C[0]: PLAN_C[1]}
        expected_second = compute_proration(
            old_monthly=price_by_code[first.new_plan_code],
            new_monthly=price_by_code[second.new_plan_code],
            period_start=PERIOD_START,
            period_end=PERIOD_END,
            change_at=CHANGE_AT,
        )
        assert second.proration.amount == expected_second.amount, (
            f"second racer's proration ({second.proration.amount}) must reflect the "
            f"real prior plan ({first.new_plan_code}), not a stale baseline — "
            f"expected {expected_second.amount}"
        )
        # Sanity: this would NOT hold under the old bug, where both racers
        # compute off plan A regardless of order.
        buggy_second = compute_proration(
            old_monthly=PLAN_A[1],
            new_monthly=price_by_code[second.new_plan_code],
            period_start=PERIOD_START,
            period_end=PERIOD_END,
            change_at=CHANGE_AT,
        )
        assert (
            second.proration.amount != buggy_second.amount
            or price_by_code[first.new_plan_code] == PLAN_A[1]
        )

        # Exactly one live subscription row for the org — a race must never
        # fork into two rows.
        async with realdb.control_sessionmaker()() as s:
            sub_query = select(Subscription).where(Subscription.organization_id == org_id)
            subs = (await s.execute(sub_query)).scalars().all()
            assert len(subs) == 1, f"expected exactly 1 subscription row, got {len(subs)}"
            final_plan = (
                await s.execute(select(Plan).where(Plan.id == subs[0].plan_id))
            ).scalar_one()
            # The final persisted state is whichever racer committed last —
            # necessarily the "second" one we identified above.
            assert final_plan.code == second.new_plan_code

        # Exactly two coherent audit rows: A->first and first->second. Never
        # two rows both claiming from_plan=A (the incoherent-trail symptom
        # the issue calls out).
        async with realdb.sessionmaker("a")() as s:
            rows = (
                (
                    await s.execute(
                        select(AuditLog.details).where(
                            AuditLog.action == "billing.plan_changed",
                            AuditLog.entity_id == sub_id,
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(rows) == 2
        from_plans = sorted(r["from_plan"] for r in rows)
        assert from_plans == sorted([PLAN_A[0], first.new_plan_code])
    finally:
        await _cleanup(realdb, org_id)
