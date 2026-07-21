"""services/billing/plan_catalog.py — default catalog + baseline Subscription.

Before this module, nothing in the app ever created a Plan or Subscription
row outside of tests (issue #180): every org's `require_entitlement` /
`require_api_entitlement` check silently failed closed, and
`services/billing/plan_change.py::change_plan` 404'd with "no live
subscription" for every org — an admin could never even upgrade. See
`test_tenant_provisioning.py::test_provision_tenant_org_can_upgrade_out_of_free`
for the end-to-end proof; this file covers `plan_catalog.py`'s own idempotency
in isolation.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import delete, select

from app.models.billing import Plan, Subscription
from app.services.billing.plan_catalog import (
    DEFAULT_PLAN_CATALOG,
    ensure_plan_catalog,
    ensure_subscription,
)


async def _clear_catalog(realdb) -> None:
    codes = [spec["code"] for spec in DEFAULT_PLAN_CATALOG]
    ctrl_mk = realdb.control_sessionmaker()
    async with ctrl_mk() as s:
        # Subscriptions FK to plans — clear any test-created ones first.
        plan_ids = (await s.execute(select(Plan.id).where(Plan.code.in_(codes)))).scalars().all()
        if plan_ids:
            await s.execute(delete(Subscription).where(Subscription.plan_id.in_(plan_ids)))
        await s.execute(delete(Plan).where(Plan.code.in_(codes)))
        await s.commit()


async def test_ensure_plan_catalog_creates_all_default_plans(realdb):
    ctrl_mk = realdb.control_sessionmaker()
    await _clear_catalog(realdb)
    try:
        async with ctrl_mk() as s:
            by_code = await ensure_plan_catalog(s)
            await s.commit()

        assert set(by_code) == {"free", "growth", "scale"}
        assert by_code["free"].entitlements == {}
        assert by_code["growth"].entitlements.get("public_api") is True
        assert by_code["scale"].entitlements.get("public_api") is True

        async with ctrl_mk() as s:
            rows = (await s.execute(select(Plan).where(Plan.code == "free"))).scalars().all()
        assert len(rows) == 1
    finally:
        await _clear_catalog(realdb)


async def test_ensure_plan_catalog_is_idempotent_and_preserves_edits(realdb):
    """A second call must not duplicate rows, and must not clobber a plan an
    operator has since edited (price/entitlements) — it only fills gaps."""
    ctrl_mk = realdb.control_sessionmaker()
    await _clear_catalog(realdb)
    try:
        async with ctrl_mk() as s:
            await ensure_plan_catalog(s)
            await s.commit()

        # Operator edits the seeded "free" plan's price.
        async with ctrl_mk() as s:
            free_plan = (await s.execute(select(Plan).where(Plan.code == "free"))).scalar_one()
            free_plan.monthly_price = Decimal("9.99")
            await s.commit()

        async with ctrl_mk() as s:
            by_code = await ensure_plan_catalog(s)
            await s.commit()

        assert by_code["free"].monthly_price == Decimal("9.99")

        async with ctrl_mk() as s:
            count = len((await s.execute(select(Plan).where(Plan.code == "free"))).scalars().all())
        assert count == 1
    finally:
        await _clear_catalog(realdb)


async def test_ensure_subscription_creates_then_is_a_noop(realdb):
    ctrl_mk = realdb.control_sessionmaker()
    org_id = realdb.info("a").org_id
    await _clear_catalog(realdb)
    try:
        async with ctrl_mk() as s:
            await ensure_plan_catalog(s)
            await s.commit()

        async with ctrl_mk() as s:
            sub1 = await ensure_subscription(s, organization_id=org_id, plan_code="free")
            await s.commit()
        assert sub1 is not None
        assert sub1.status == "active"

        # Calling again (e.g. a re-seed) must not create a second live row.
        async with ctrl_mk() as s:
            sub2 = await ensure_subscription(s, organization_id=org_id, plan_code="free")
            await s.commit()
        assert sub2.id == sub1.id

        async with ctrl_mk() as s:
            live_count = (
                (
                    await s.execute(
                        select(Subscription).where(
                            Subscription.organization_id == org_id,
                            Subscription.status != "canceled",
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(live_count) == 1
    finally:
        async with ctrl_mk() as s:
            await s.execute(delete(Subscription).where(Subscription.organization_id == org_id))
            await s.commit()
        await _clear_catalog(realdb)


async def test_ensure_subscription_returns_none_for_unknown_plan_code(realdb):
    """A fresh control DB before ensure_plan_catalog has run, or a caller
    passing a code that was never seeded, must not crash provisioning —
    mirrors the admin-role skip-silently pattern already used elsewhere in
    tenant_provisioning."""
    ctrl_mk = realdb.control_sessionmaker()
    org_id = uuid.uuid4()  # doesn't need to be a real org for this pure check
    async with ctrl_mk() as s:
        result = await ensure_subscription(
            s, organization_id=org_id, plan_code=f"nonexistent_{uuid.uuid4().hex[:8]}"
        )
    assert result is None
