"""Per-row isolation + failure accounting for the billing dunning sweep.

Three properties, all of which the pre-fix sweep lacked despite its module
docstring claiming the first:

1. One poisoned ``past_due`` row must not abort the rest of the tick. There was
   no per-row guard at all — anything raising (most obviously the shared
   ``control_db.commit()``) took every remaining row with it.
2. The tick must be able to REPORT that failure. ``_dunning_tick`` returned a
   bare ``int``, which ``sweep_health.extract_counts`` maps to ``{"count": n}``
   — no ``failures`` key, so ``failure_count`` summed to zero and this sweep
   could never be anything but ``ok`` short of the tick raising outright.
3. A swallowed audit write is a failure, not a silent success. The cancellation
   committed whether or not ``dispatch_auth_audit`` (fail-soft by design) had
   actually written the ``billing.subscription_canceled`` row.

The sweep still never moves money — it only flips a Subscription status.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from app.config import settings
from app.models.billing import Plan, Subscription
from app.models.workflow import AuditLog
from app.services import sweep_health
from app.services.billing import dunning_sweep
from app.services.billing.dunning_sweep import DunningResult, run_dunning_once
from app.services.sweep_health import (
    OUTCOME_PARTIAL,
    SWEEP_BILLING_DUNNING,
    extract_counts,
    failure_count,
)

_PLAN_PREFIX = "dunres_"


@pytest.fixture
def _audit_engine_on_loop(monkeypatch, realdb):
    """Write the sweep's tenant audit row on THIS test's event loop.

    Same arrangement as ``test_billing_webhook``'s fixture: the global tenant
    engine pool is bound to whichever loop first touched it, so a realdb test on
    a fresh loop would hit "connection closed / different loop". NullPool engines
    hold nothing across tests.
    """
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from app.database import _make_tenant_url

    def _engine(db_name: str):
        return create_async_engine(_make_tenant_url(db_name), poolclass=NullPool)

    monkeypatch.setattr("app.database.get_tenant_engine", _engine)


async def _clear_subscriptions(realdb):
    async with realdb.control_sessionmaker()() as s:
        await s.execute(delete(Subscription))
        await s.execute(delete(Plan).where(Plan.code.like(f"{_PLAN_PREFIX}%")))
        await s.commit()


async def _seed_past_due(realdb, org_id, *, sub_id, external_id, period_end) -> uuid.UUID:
    async with realdb.control_sessionmaker()() as s:
        plan_id = uuid.uuid4()
        s.add(
            Plan(
                id=plan_id,
                code=f"{_PLAN_PREFIX}{external_id}",
                name="DunRes",
                monthly_price=Decimal("49.00"),
                currency="USD",
            )
        )
        s.add(
            Subscription(
                id=sub_id,
                organization_id=org_id,
                plan_id=plan_id,
                status="past_due",
                external_subscription_id=external_id,
                current_period_end=period_end,
            )
        )
        await s.commit()
    return sub_id


async def _statuses(realdb, ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    async with realdb.control_sessionmaker()() as s:
        rows = (
            await s.execute(
                select(Subscription.id, Subscription.status).where(Subscription.id.in_(ids))
            )
        ).all()
    return {rid: status for rid, status in rows}


async def _audit_actions(realdb, sub_id) -> list[str]:
    async with realdb.sessionmaker("a")() as s:
        return list(
            (
                await s.execute(
                    select(AuditLog.action).where(
                        AuditLog.action.like("billing.subscription_%"),
                        AuditLog.entity_id == sub_id,
                    )
                )
            )
            .scalars()
            .all()
        )


# ---------------------------------------------------------------------------
# Pure — the failure counter actually reaches the health registry
# ---------------------------------------------------------------------------


def test_failures_reach_sweep_health_and_degrade_the_sweep(monkeypatch):
    """A tick that completed while a row failed is not ``ok``.

    Pre-fix the tick returned a bare int, so ``extract_counts`` produced
    ``{"count": n}`` and ``failure_count`` was always 0 — this sweep could not
    report a partial failure at all.
    """
    counts = extract_counts(DunningResult(subscriptions_scanned=3, canceled=2, failures=1))
    assert counts == {"subscriptions_scanned": 3, "canceled": 2, "failures": 1}
    assert failure_count(counts) == 1

    monkeypatch.setattr(settings, "billing_dunning_enabled", True)
    sweep_health.reset()
    try:
        health = None
        for _ in range(sweep_health.alert_streak()):
            health = sweep_health.run_succeeded(
                SWEEP_BILLING_DUNNING, DunningResult(subscriptions_scanned=1, failures=1)
            )
        assert health is not None
        assert health.last_outcome == OUTCOME_PARTIAL
        assert sweep_health.overall_state([health]) == "degraded"
    finally:
        sweep_health.reset()


# ---------------------------------------------------------------------------
# realdb — per-row isolation against the real control plane
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_one_poisoned_row_does_not_abort_the_tick(realdb, monkeypatch, _audit_engine_on_loop):
    """The other overdue subscriptions still cancel, and the failure is counted."""
    monkeypatch.setattr(settings, "billing_dunning_grace_days", 14)
    org_id = realdb.info("a").org_id
    now = datetime.now(UTC)
    await _clear_subscriptions(realdb)
    try:
        # Rows are processed in id order; poison the MIDDLE one so the test
        # proves both that an earlier commit survived and that a later row still
        # ran (pre-fix, the raise ended the loop).
        ids = sorted(uuid.uuid4() for _ in range(3))
        for i, sub_id in enumerate(ids):
            await _seed_past_due(
                realdb,
                org_id,
                sub_id=sub_id,
                external_id=f"sub_res_{i}",
                period_end=now - timedelta(days=30),
            )

        real_audit = dunning_sweep._record_cancellation_audit

        async def _poisoned(control_db, *, subscription, previous):
            if subscription.id == ids[1]:
                raise RuntimeError("tenant audit sink unavailable")
            return await real_audit(control_db, subscription=subscription, previous=previous)

        monkeypatch.setattr(dunning_sweep, "_record_cancellation_audit", _poisoned)

        async with realdb.control_sessionmaker()() as s:
            result = await run_dunning_once(s, now=now)

        assert isinstance(result, DunningResult)
        assert result.subscriptions_scanned == 3
        assert result.canceled == 2
        assert result.failures == 1

        statuses = await _statuses(realdb, ids)
        assert statuses[ids[0]] == "canceled"
        assert statuses[ids[1]] == "past_due"  # rolled back, retried next tick
        assert statuses[ids[2]] == "canceled"  # ran despite the poison before it

        # The two real cancellations left their trail; the failed one did not.
        assert "billing.subscription_canceled" in await _audit_actions(realdb, ids[0])
        assert "billing.subscription_canceled" in await _audit_actions(realdb, ids[2])
        assert await _audit_actions(realdb, ids[1]) == []
    finally:
        await _clear_subscriptions(realdb)


@pytest.mark.asyncio
async def test_failed_audit_write_rolls_the_cancellation_back(realdb, monkeypatch):
    """No cancellation without its audit row.

    Pre-fix the row went through ``dispatch_auth_audit``, which swallows every
    exception, so the status change committed with nothing in the trail and the
    tick reported a clean success.
    """
    monkeypatch.setattr(settings, "billing_dunning_grace_days", 14)
    org_id = realdb.info("a").org_id
    now = datetime.now(UTC)
    await _clear_subscriptions(realdb)
    try:
        sub_id = uuid.uuid4()
        await _seed_past_due(
            realdb,
            org_id,
            sub_id=sub_id,
            external_id="sub_res_audit",
            period_end=now - timedelta(days=30),
        )

        async def _boom(*args, **kwargs):
            raise RuntimeError("audit_log write refused")

        monkeypatch.setattr(dunning_sweep, "dispatch_audit", _boom)

        async with realdb.control_sessionmaker()() as s:
            result = await run_dunning_once(s, now=now)

        assert result.canceled == 0
        assert result.failures == 1
        assert (await _statuses(realdb, [sub_id]))[sub_id] == "past_due"
        assert await _audit_actions(realdb, sub_id) == []
    finally:
        await _clear_subscriptions(realdb)


@pytest.mark.asyncio
async def test_row_within_grace_is_neither_canceled_nor_a_failure(
    realdb, monkeypatch, _audit_engine_on_loop
):
    """The spare path is still a clean no-op — it must not read as a failure."""
    monkeypatch.setattr(settings, "billing_dunning_grace_days", 14)
    org_id = realdb.info("a").org_id
    now = datetime.now(UTC)
    await _clear_subscriptions(realdb)
    try:
        spared, overdue = uuid.uuid4(), uuid.uuid4()
        await _seed_past_due(
            realdb,
            org_id,
            sub_id=spared,
            external_id="sub_res_grace",
            period_end=now - timedelta(days=3),
        )
        await _seed_past_due(
            realdb,
            org_id,
            sub_id=overdue,
            external_id="sub_res_overdue",
            period_end=now - timedelta(days=30),
        )

        async with realdb.control_sessionmaker()() as s:
            result = await run_dunning_once(s, now=now)

        assert result.subscriptions_scanned == 2
        assert result.canceled == 1
        assert result.failures == 0
        statuses = await _statuses(realdb, [spared, overdue])
        assert statuses[spared] == "past_due"
        assert statuses[overdue] == "canceled"
    finally:
        await _clear_subscriptions(realdb)
