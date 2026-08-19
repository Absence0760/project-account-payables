"""AI Cash-Flow Copilot — saved plans, plan-vs-actual, and consolidated mode.

Covers the two deferred Phase 3 sub-items (``docs/cash-flow-copilot.md`` §12):

  1. **Saved plans / plan-vs-actual** — the tenant-scoped ``CashPlan`` snapshot
     (migration ``0087_cash_plans``) and the five routes over it
     (``save`` / list / detail / ``variance`` / delete).
  2. **Consolidated cross-entity mode** — a whole-group plan (``entity_id`` is
     ``None``), the same posture ``services/cash_flow_alerts`` already takes.

The load-bearing invariants proven here:

  - **The deterministic ``plan_id`` is still the key.** Persisting a plan does
    not change how one is acted on: ``payment_runs.plan_id`` remains the
    draft-run idempotency anchor, and the stale-plan 409 still fires on a
    replay body that no longer hashes to the URL's id.
  - **A saved plan is a FROZEN baseline.** A second save returns the existing
    snapshot untouched (``created=False``) rather than restating it against
    newer data — restating would rewrite the very thing a variance measures
    against.
  - **Money is exact.** Every monetary field in every response is an exact
    decimal STRING; the frozen JSONB curve stores strings, never JSON numbers,
    so nothing round-trips through ``float``.
  - **Only elapsed periods are scored.** An in-progress or future period has no
    variance to report and is excluded from every total.
  - **Consolidated scope is discovered from the plan id, never asserted by the
    client**, and the variance runs under the SAVED plan's own scope.
  - **RBAC + kill switch + tenant isolation** hold on the new surface exactly
    as on the enact routes.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

# ===========================================================================
# Helpers (mirror the realdb seeding patterns in test_cash_flow_copilot.py).
# ===========================================================================


async def _default_entity_id(session, org_id):
    from sqlalchemy import text

    row = (
        await session.execute(
            text("SELECT id FROM entities WHERE organization_id = :o AND is_default"),
            {"o": org_id},
        )
    ).first()
    return row[0]


async def _seed_invoice(
    session,
    org_id,
    entity_id,
    *,
    number,
    vendor_name,
    amount,
    status="approved",
    due_date=None,
    currency="USD",
):
    from app.models.invoice import Invoice

    inv = Invoice(
        id=uuid.uuid4(),
        organization_id=org_id,
        entity_id=entity_id,
        invoice_number=number,
        vendor_name=vendor_name,
        amount=Decimal(str(amount)),
        currency=currency,
        status=status,
        invoice_date=date.today(),
        due_date=due_date,
    )
    session.add(inv)
    return inv


async def _seed_completed_payment(
    session,
    entity_id,
    invoice_id,
    *,
    amount,
    completed_at,
):
    """`Payment` carries no `organization_id` — it is scoped through its
    invoice and the tenant database, which is why the variance query filters on
    entity + status only."""
    from app.models.payment import Payment

    pay = Payment(
        id=uuid.uuid4(),
        entity_id=entity_id,
        invoice_id=invoice_id,
        amount=Decimal(str(amount)),
        status="completed",
        completed_at=completed_at,
    )
    session.add(pay)
    return pay


def _assert_money_str(value, path):
    """A money field must serialise as an exact decimal STRING, not a number."""
    assert isinstance(value, str), f"{path} should be an exact string, got {type(value)}: {value!r}"
    Decimal(value)


def _assert_no_float(obj, *, path="result"):
    if isinstance(obj, float):
        raise AssertionError(f"float found at {path}: {obj!r}")
    if isinstance(obj, dict):
        for k, v in obj.items():
            _assert_no_float(v, path=f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _assert_no_float(v, path=f"{path}[{i}]")


def _replay_body(plan, *, label=None) -> dict:
    """The `CashFlowPlanSaveRequest` body a well-behaved frontend sends back —
    the plan's own RESOLVED defining fields, verbatim."""
    body = {
        "granularity": plan.granularity,
        "horizon_days": plan.horizon_days,
        "min_balance_threshold": (
            str(plan.min_balance_threshold) if plan.min_balance_threshold is not None else None
        ),
        "cash_budget": str(plan.cash_budget) if plan.cash_budget is not None else None,
        "cost_of_capital_pct": str(plan.cost_of_capital_pct),
    }
    if label is not None:
        body["label"] = label
    return body


async def _propose_plan(realdb, key="a", *, entity_id=None, **param_overrides):
    from app.services.assistant.tools.cashflow import propose_payment_plan
    from app.services.assistant.tools.schemas import ProposePaymentPlanParams

    info = realdb.info(key)
    mk = realdb.sessionmaker(key)
    ctrl_mk = realdb.control_sessionmaker()
    async with mk() as sa, ctrl_mk() as ctrl:
        return await propose_payment_plan(
            sa,
            org_id=info.org_id,
            entity_id=entity_id,
            current_user_id=info.users["admin"],
            control_db=ctrl,
            params=ProposePaymentPlanParams(granularity="week", **param_overrides),
        )


# ===========================================================================
# 1. Pure — period-label bounds, freeze/thaw, and the variance comparison
# ===========================================================================


@pytest.mark.parametrize("granularity", ["day", "week", "month"])
def test_period_bounds_for_label_round_trips_the_canonical_bucketer(granularity):
    """`period_bounds_for_label` must agree with `analytics._period_bounds` for
    every label that function can emit — it delegates rather than
    reimplementing, and this is the guard that keeps that true."""
    from app.services.analytics import _period_bounds
    from app.services.cash_flow_plan import period_bounds_for_label

    day = date(2026, 1, 1)
    for offset in range(0, 400, 7):
        d = day + timedelta(days=offset)
        label, start, end = _period_bounds(d, granularity)
        assert period_bounds_for_label(label, granularity) == (start, end)


def test_period_bounds_for_label_refuses_a_label_from_another_granularity():
    """A corrupt / mismatched snapshot must fail loudly — guessing a window for
    it would silently mis-date somebody's variance."""
    from app.services.cash_flow_plan import period_bounds_for_label

    # A Wednesday is a valid `day` label but never a `week` label (which is
    # always the period's Monday).
    with pytest.raises(ValueError):
        period_bounds_for_label("2026-01-07", "week")


def test_freeze_periods_stores_exact_strings_and_thaw_round_trips():
    from app.services.cash_flow_plan import PlanPeriod, freeze_periods, thaw_periods

    periods = [
        PlanPeriod(
            period="2026-01-05",
            period_start=date(2026, 1, 5),
            period_end=date(2026, 1, 11),
            opening=Decimal("250000.00"),
            outflow=Decimal("1234.56"),
            closing=Decimal("248765.44"),
            below_threshold=False,
            unconverted_count=1,
        )
    ]
    frozen = freeze_periods(periods, "week")
    _assert_no_float(frozen, path="frozen")
    for money_key in ("opening", "outflow", "closing"):
        _assert_money_str(frozen[0][money_key], f"frozen[0].{money_key}")
    assert frozen[0]["period_start"] == "2026-01-05"
    assert frozen[0]["period_end"] == "2026-01-11"
    assert thaw_periods(frozen) == periods


def test_freeze_periods_derives_bounds_when_the_source_has_none():
    """The assistant tool's `PaymentPlanPeriod` carries no bounds; freezing must
    derive them from the label rather than dropping them."""
    from types import SimpleNamespace

    from app.services.cash_flow_plan import freeze_periods

    tool_period = SimpleNamespace(
        period="2026-03",
        opening=Decimal("10"),
        outflow=Decimal("4"),
        closing=Decimal("6"),
        below_threshold=True,
        unconverted_count=0,
    )
    frozen = freeze_periods([tool_period], "month")
    assert frozen[0]["period_start"] == "2026-03-01"
    assert frozen[0]["period_end"] == "2026-03-31"
    assert frozen[0]["below_threshold"] is True


def test_compare_plan_to_actual_scores_only_elapsed_periods():
    from app.services.cash_flow_plan import PlanPeriod, compare_plan_to_actual

    def _p(label, start, end, outflow):
        return PlanPeriod(
            period=label,
            period_start=start,
            period_end=end,
            opening=Decimal("0"),
            outflow=Decimal(outflow),
            closing=Decimal("0"),
            below_threshold=False,
        )

    as_of = date(2026, 2, 10)
    planned = [
        _p("2026-01-26", date(2026, 1, 26), date(2026, 2, 1), "100.00"),  # elapsed
        _p("2026-02-09", date(2026, 2, 9), date(2026, 2, 15), "200.00"),  # in progress
        _p("2026-02-16", date(2026, 2, 16), date(2026, 2, 22), "300.00"),  # future
    ]
    actual = {
        "2026-01-26": Decimal("140.00"),
        "2026-02-09": Decimal("50.00"),
        # Real cash in a week the plan never projected.
        "2026-02-02": Decimal("77.00"),
    }

    result = compare_plan_to_actual(planned, actual, as_of=as_of)

    assert [p.status for p in result.periods] == ["elapsed", "in_progress", "future"]
    # Totals cover the ELAPSED period only — a partial actual must never be
    # scored against a whole projection.
    assert result.planned_total == Decimal("100.00")
    assert result.actual_total == Decimal("140.00")
    assert result.variance_total == Decimal("40.00")
    assert result.elapsed_period_count == 1
    assert result.open_period_count == 2
    # …but the in-progress figure is still shown.
    assert result.periods[1].actual_outflow == Decimal("50.00")
    assert result.periods[1].variance == Decimal("-150.00")
    # Cash in a period the plan doesn't contain is surfaced, never absorbed.
    assert result.unmatched_actual_periods == ["2026-02-02"]
    assert result.unmatched_actual_total == Decimal("77.00")


def test_compare_plan_to_actual_handles_an_empty_plan():
    from app.services.cash_flow_plan import compare_plan_to_actual

    result = compare_plan_to_actual([], {}, as_of=date(2026, 2, 10))
    assert result.periods == []
    assert result.planned_total == Decimal("0")
    assert result.variance_total == Decimal("0")
    assert result.elapsed_period_count == 0


# ===========================================================================
# 2. Save — snapshot creation, idempotency, exact money, the stale guard
# ===========================================================================


async def test_save_freezes_the_plan_and_is_idempotent(realdb):
    from app.models.cash_plan import CashPlan

    a = realdb.info("a")
    mk_a = realdb.sessionmaker("a")
    async with mk_a() as sa:
        ent = await _default_entity_id(sa, a.org_id)
        await _seed_invoice(
            sa,
            a.org_id,
            ent,
            number="SAVE-1",
            vendor_name="SaveCo",
            amount="900.00",
            due_date=date.today() + timedelta(days=10),
        )
        await sa.commit()

    plan = await _propose_plan(realdb)
    body = _replay_body(plan, label="  Q1 baseline  ")

    async with realdb.client(key="a", role="admin") as c:
        r1 = await c.post(f"/api/cash-flow/plans/{plan.plan_id}/save", json=body)
        assert r1.status_code == 201, r1.text
        first = r1.json()
        assert first["created"] is True
        saved = first["plan"]
        assert saved["plan_id"] == plan.plan_id
        assert saved["label"] == "Q1 baseline"  # trimmed
        assert saved["consolidated"] is True  # proposed with entity_id=None
        assert saved["entity_id"] is None
        assert saved["period_count"] == len(saved["periods"]) == len(plan.periods)
        assert saved["has_draft_run"] is False
        _assert_no_float(first, path="save")
        for money_key in ("opening_balance", "total_savings_selected", "total_outlay_selected"):
            _assert_money_str(saved[money_key], f"plan.{money_key}")
        for i, period in enumerate(saved["periods"]):
            for money_key in ("opening", "outflow", "closing"):
                _assert_money_str(period[money_key], f"plan.periods[{i}].{money_key}")
            assert period["period_start"] <= period["period_end"]

        # A repeat save returns the SAME frozen snapshot, never a second row and
        # never a restatement against newer data.
        r2 = await c.post(f"/api/cash-flow/plans/{plan.plan_id}/save", json=body)
        assert r2.status_code == 200, r2.text
        second = r2.json()
        assert second["created"] is False
        assert second["plan"] == saved

    async with mk_a() as sa:
        rows = (
            (await sa.execute(select(CashPlan).where(CashPlan.plan_id == plan.plan_id)))
            .scalars()
            .all()
        )
        assert len(rows) == 1, "one snapshot per plan_id — the unique index is the anchor"


async def test_save_does_not_restate_a_snapshot_when_the_data_moves(realdb):
    """The point of a frozen baseline: new commitments after the save must not
    change what the saved plan says it projected."""
    a = realdb.info("a")
    mk_a = realdb.sessionmaker("a")
    async with mk_a() as sa:
        ent = await _default_entity_id(sa, a.org_id)
        await _seed_invoice(
            sa,
            a.org_id,
            ent,
            number="FROZEN-1",
            vendor_name="FrozenCo",
            amount="500.00",
            due_date=date.today() + timedelta(days=5),
        )
        await sa.commit()

    plan = await _propose_plan(realdb)
    body = _replay_body(plan)

    async with realdb.client(key="a", role="admin") as c:
        first = (await c.post(f"/api/cash-flow/plans/{plan.plan_id}/save", json=body)).json()

    async with mk_a() as sa:
        ent = await _default_entity_id(sa, a.org_id)
        await _seed_invoice(
            sa,
            a.org_id,
            ent,
            number="FROZEN-2",
            vendor_name="FrozenCo",
            amount="9999.00",
            due_date=date.today() + timedelta(days=5),
        )
        await sa.commit()

    async with realdb.client(key="a", role="admin") as c:
        again = (await c.post(f"/api/cash-flow/plans/{plan.plan_id}/save", json=body)).json()
        detail = (await c.get(f"/api/cash-flow/plans/{plan.plan_id}")).json()

    assert again["created"] is False
    assert again["plan"] == first["plan"]
    assert detail["periods"] == first["plan"]["periods"]


async def test_save_refuses_a_stale_plan_id(realdb):
    """The stale-plan guard is unchanged by persistence: a replay body that no
    longer hashes to the URL's plan_id is a clean 409, and nothing is stored."""
    from app.models.cash_plan import CashPlan

    plan = await _propose_plan(realdb)
    body = _replay_body(plan)
    body["horizon_days"] = (plan.horizon_days or 90) + 7  # tampered

    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post(f"/api/cash-flow/plans/{plan.plan_id}/save", json=body)
    assert resp.status_code == 409, resp.text

    mk_a = realdb.sessionmaker("a")
    async with mk_a() as sa:
        assert (await sa.execute(select(CashPlan))).scalars().all() == []


async def test_saving_never_moves_money(realdb):
    """Save is read-only over the money path: no Payment, no PaymentRun, and no
    invoice leaves `approved`."""
    from app.models.invoice import Invoice
    from app.models.payment import Payment, PaymentRun

    a = realdb.info("a")
    mk_a = realdb.sessionmaker("a")
    async with mk_a() as sa:
        ent = await _default_entity_id(sa, a.org_id)
        inv = await _seed_invoice(
            sa,
            a.org_id,
            ent,
            number="NOMOVE-1",
            vendor_name="NoMoveCo",
            amount="750.00",
            due_date=date.today() + timedelta(days=3),
        )
        await sa.commit()
        inv_id = inv.id

    plan = await _propose_plan(realdb)
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post(f"/api/cash-flow/plans/{plan.plan_id}/save", json=_replay_body(plan))
    assert resp.status_code == 201, resp.text

    async with mk_a() as sa:
        assert (await sa.execute(select(Payment))).scalars().all() == []
        assert (await sa.execute(select(PaymentRun))).scalars().all() == []
        refreshed = await sa.get(Invoice, inv_id)
        assert refreshed.status == "approved"


async def test_save_writes_a_pii_free_audit_row(realdb):
    from app.models.workflow import AuditLog

    plan = await _propose_plan(realdb)
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post(f"/api/cash-flow/plans/{plan.plan_id}/save", json=_replay_body(plan))
    assert resp.status_code == 201, resp.text

    mk_a = realdb.sessionmaker("a")
    async with mk_a() as sa:
        rows = (
            (await sa.execute(select(AuditLog).where(AuditLog.action == "cash_plan.saved")))
            .scalars()
            .all()
        )
    assert len(rows) == 1
    details = rows[0].details or {}
    assert details["plan_id"] == plan.plan_id
    assert details["consolidated"] is True
    # PII-free: shape only — no vendor, no invoice number, no amount.
    assert set(details) == {
        "plan_id",
        "granularity",
        "horizon_days",
        "period_count",
        "consolidated",
    }


# ===========================================================================
# 3. List / detail / delete — scoping, RBAC, kill switch, tenant isolation
# ===========================================================================


async def test_list_detail_and_delete_round_trip(realdb):
    from app.models.cash_plan import CashPlan
    from app.models.workflow import AuditLog

    plan = await _propose_plan(realdb)
    async with realdb.client(key="a", role="cfo") as c:
        assert (
            await c.post(f"/api/cash-flow/plans/{plan.plan_id}/save", json=_replay_body(plan))
        ).status_code == 201

        listed = await c.get("/api/cash-flow/plans")
        assert listed.status_code == 200, listed.text
        rows = listed.json()
        assert [r["plan_id"] for r in rows] == [plan.plan_id]
        assert "periods" not in rows[0], "the list row is a summary, not the whole curve"

        detail = await c.get(f"/api/cash-flow/plans/{plan.plan_id}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["plan_id"] == plan.plan_id

        gone = await c.delete(f"/api/cash-flow/plans/{plan.plan_id}")
        assert gone.status_code == 204, gone.text
        assert (await c.get(f"/api/cash-flow/plans/{plan.plan_id}")).status_code == 404
        assert (await c.get("/api/cash-flow/plans")).json() == []

    mk_a = realdb.sessionmaker("a")
    async with mk_a() as sa:
        assert (await sa.execute(select(CashPlan))).scalars().all() == []
        actions = {
            row.action
            for row in (await sa.execute(select(AuditLog))).scalars().all()
            if row.action.startswith("cash_plan.")
        }
    assert actions == {"cash_plan.saved", "cash_plan.deleted"}


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/cash-flow/plans"),
        ("get", "/api/cash-flow/plans/whatever"),
        ("get", "/api/cash-flow/plans/whatever/variance"),
        ("delete", "/api/cash-flow/plans/whatever"),
    ],
)
async def test_saved_plan_routes_forbid_ap_clerk(realdb, method, path):
    """Same finance-leader gate as the enact routes — an ap_clerk gets a clean
    403, never data and never a 500, even against a bogus id."""
    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await getattr(c, method)(path)
    assert resp.status_code == 403, resp.text


async def test_save_forbids_ap_clerk(realdb):
    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await c.post(
            "/api/cash-flow/plans/whatever/save",
            json={
                "granularity": "week",
                "horizon_days": 90,
                "min_balance_threshold": None,
                "cash_budget": None,
                "cost_of_capital_pct": "8.0",
            },
        )
    assert resp.status_code == 403, resp.text


async def test_saved_plan_routes_404_when_the_copilot_is_disabled(realdb, monkeypatch):
    from app.config import settings

    plan = await _propose_plan(realdb)
    async with realdb.client(key="a", role="admin") as c:
        assert (
            await c.post(f"/api/cash-flow/plans/{plan.plan_id}/save", json=_replay_body(plan))
        ).status_code == 201

        monkeypatch.setattr(settings, "cashflow_copilot_enabled", False)
        assert (await c.get("/api/cash-flow/plans")).status_code == 404
        assert (await c.get(f"/api/cash-flow/plans/{plan.plan_id}")).status_code == 404
        assert (await c.get(f"/api/cash-flow/plans/{plan.plan_id}/variance")).status_code == 404


async def test_tenant_b_cannot_read_tenant_as_saved_plan(realdb):
    """Tenant isolation at the data layer — the snapshot lives in tenant A's own
    database, so tenant B's identical plan_id is the same opaque 404."""
    plan = await _propose_plan(realdb, "a")
    async with realdb.client(key="a", role="admin") as c:
        assert (
            await c.post(f"/api/cash-flow/plans/{plan.plan_id}/save", json=_replay_body(plan))
        ).status_code == 201

    async with realdb.client(key="b", role="admin") as c:
        assert (await c.get(f"/api/cash-flow/plans/{plan.plan_id}")).status_code == 404
        assert (await c.get("/api/cash-flow/plans")).json() == []


# ===========================================================================
# 4. Consolidated cross-entity mode
# ===========================================================================


async def _seed_second_entity(session, org_id, *, slug="sub"):
    from app.models.entity import Entity

    ent = Entity(
        id=uuid.uuid4(),
        organization_id=org_id,
        name="Subsidiary",
        slug=slug,
        is_default=False,
        is_active=True,
    )
    session.add(ent)
    return ent


async def test_consolidated_plan_is_discovered_while_an_entity_is_selected(realdb):
    """A whole-group plan carries the consolidated plan_id. Saving it with an
    entity SELECTED must still resolve — the scope comes from the id, not from
    `X-Entity-ID` — and the snapshot is stored consolidated."""
    a = realdb.info("a")
    mk_a = realdb.sessionmaker("a")
    async with mk_a() as sa:
        ent = await _default_entity_id(sa, a.org_id)
        await _seed_invoice(
            sa,
            a.org_id,
            ent,
            number="CONS-1",
            vendor_name="ConsCo",
            amount="400.00",
            due_date=date.today() + timedelta(days=6),
        )
        await sa.commit()

    plan = await _propose_plan(realdb, entity_id=None)  # consolidated
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post(
            f"/api/cash-flow/plans/{plan.plan_id}/save",
            json=_replay_body(plan),
            headers={"X-Entity-ID": str(ent)},
        )
    assert resp.status_code == 201, resp.text
    saved = resp.json()["plan"]
    assert saved["consolidated"] is True
    assert saved["entity_id"] is None


async def test_entity_scoped_plan_is_stored_and_listed_under_that_entity(realdb):
    """The mirror case: a plan proposed under one entity keeps that scope, is
    listed for it, and is NOT listed under a different entity."""
    a = realdb.info("a")
    mk_a = realdb.sessionmaker("a")
    async with mk_a() as sa:
        ent = await _default_entity_id(sa, a.org_id)
        other = await _seed_second_entity(sa, a.org_id)
        await _seed_invoice(
            sa,
            a.org_id,
            ent,
            number="ENT-1",
            vendor_name="EntCo",
            amount="620.00",
            due_date=date.today() + timedelta(days=4),
        )
        await sa.commit()
        other_id = other.id

    plan = await _propose_plan(realdb, entity_id=ent)
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post(
            f"/api/cash-flow/plans/{plan.plan_id}/save",
            json=_replay_body(plan),
            headers={"X-Entity-ID": str(ent)},
        )
        assert resp.status_code == 201, resp.text
        saved = resp.json()["plan"]
        assert saved["consolidated"] is False
        assert saved["entity_id"] == str(ent)

        mine = await c.get("/api/cash-flow/plans", headers={"X-Entity-ID": str(ent)})
        assert [r["plan_id"] for r in mine.json()] == [plan.plan_id]

        theirs = await c.get("/api/cash-flow/plans", headers={"X-Entity-ID": str(other_id)})
        assert theirs.json() == []

        # `?consolidated=true` ignores the selector and shows the whole tenant.
        everything = await c.get(
            "/api/cash-flow/plans?consolidated=true", headers={"X-Entity-ID": str(other_id)}
        )
        assert [r["plan_id"] for r in everything.json()] == [plan.plan_id]


async def test_an_entity_plan_id_is_not_accepted_under_a_different_entity(realdb):
    """The two-candidate scope discovery must not become a wildcard: an id built
    under entity A is neither entity B's id nor the consolidated one."""
    a = realdb.info("a")
    mk_a = realdb.sessionmaker("a")
    async with mk_a() as sa:
        ent = await _default_entity_id(sa, a.org_id)
        other = await _seed_second_entity(sa, a.org_id, slug="sub2")
        await sa.commit()
        other_id = other.id

    plan = await _propose_plan(realdb, entity_id=ent)
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post(
            f"/api/cash-flow/plans/{plan.plan_id}/save",
            json=_replay_body(plan),
            headers={"X-Entity-ID": str(other_id)},
        )
    assert resp.status_code == 409, resp.text


async def test_copilot_consolidated_flag_runs_the_turn_org_wide(realdb, monkeypatch):
    """`?consolidated=true` on the chat route overrides the entity selector, so
    the tools (and therefore any plan proposed in that turn) run org-wide."""
    from types import SimpleNamespace

    import app.api.cash_flow as cash_flow_api

    a = realdb.info("a")
    mk_a = realdb.sessionmaker("a")
    async with mk_a() as sa:
        ent = await _default_entity_id(sa, a.org_id)

    seen: list = []

    async def _fake_run_turn(**kwargs):
        seen.append(kwargs["entity_id"])
        reply = SimpleNamespace(answer="ok", tool_invocations=[], input_tokens=1, output_tokens=1)
        return reply, uuid.uuid4()

    monkeypatch.setattr(cash_flow_api, "run_turn", _fake_run_turn)

    async with realdb.client(key="a", role="admin") as c:
        scoped = await c.post(
            "/api/cash-flow/copilot",
            json={"message": "cash?"},
            headers={"X-Entity-ID": str(ent)},
        )
        assert scoped.status_code == 200, scoped.text
        group = await c.post(
            "/api/cash-flow/copilot?consolidated=true",
            json={"message": "cash?"},
            headers={"X-Entity-ID": str(ent)},
        )
        assert group.status_code == 200, group.text

    assert seen == [ent, None]


# ===========================================================================
# 5. Plan-vs-actual
# ===========================================================================


async def _save_snapshot(session, org_id, *, entity_id, periods, granularity="week", plan_date):
    """Insert a CashPlan snapshot directly.

    The variance endpoint's contract is "given a saved snapshot, score it", and
    a snapshot proposed today can only ever contain in-progress/future periods —
    so exercising the ELAPSED path needs a plan whose window has closed, i.e. a
    historical row. That is exactly what this table stores.
    """
    from app.models.cash_plan import CashPlan
    from app.services.cash_flow_plan import compute_plan_id, freeze_periods

    plan_id = compute_plan_id(
        org_id=org_id,
        entity_id=entity_id,
        granularity=granularity,
        horizon_days=90,
        min_balance_threshold=None,
        cash_budget=None,
        cost_of_capital_pct=Decimal("8.0"),
        today=plan_date,
    )
    row = CashPlan(
        organization_id=org_id,
        entity_id=entity_id,
        plan_id=plan_id,
        plan_date=plan_date,
        label="historical",
        granularity=granularity,
        horizon_days=90,
        min_balance_threshold=None,
        cash_budget=None,
        cost_of_capital_pct=Decimal("8.0"),
        currency="USD",
        opening_balance=Decimal("0"),
        first_shortfall_period=None,
        total_savings_selected=Decimal("0"),
        total_outlay_selected=Decimal("0"),
        unconverted_count=0,
        periods=freeze_periods(periods, granularity),
        selected_offer_ids=[],
        unretimed_offer_ids=[],
    )
    session.add(row)
    return row


def _week(anchor: date, outflow: str):
    from app.services.analytics import _period_bounds
    from app.services.cash_flow_plan import PlanPeriod

    label, start, end = _period_bounds(anchor, "week")
    return PlanPeriod(
        period=label,
        period_start=start,
        period_end=end,
        opening=Decimal("0"),
        outflow=Decimal(outflow),
        closing=Decimal("0"),
        below_threshold=False,
    )


async def test_variance_scores_an_elapsed_period_against_real_settled_cash(realdb):
    a = realdb.info("a")
    mk_a = realdb.sessionmaker("a")
    today = datetime.now(UTC).date()
    past_anchor = today - timedelta(days=21)
    plan_date = past_anchor

    async with mk_a() as sa:
        ent = await _default_entity_id(sa, a.org_id)
        inv = await _seed_invoice(
            sa,
            a.org_id,
            ent,
            number="VAR-1",
            vendor_name="VarCo",
            amount="1000.00",
            due_date=past_anchor,
        )
        await sa.flush()
        await _seed_completed_payment(
            sa,
            ent,
            inv.id,
            amount="1250.00",
            completed_at=datetime.combine(past_anchor, datetime.min.time(), tzinfo=UTC)
            + timedelta(hours=9),
        )
        row = await _save_snapshot(
            sa,
            a.org_id,
            entity_id=None,  # consolidated snapshot
            periods=[_week(past_anchor, "1000.00"), _week(today + timedelta(days=7), "500.00")],
            plan_date=plan_date,
        )
        await sa.commit()
        plan_id = row.plan_id

    async with realdb.client(key="a", role="admin") as c:
        resp = await c.get(f"/api/cash-flow/plans/{plan_id}/variance")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    _assert_no_float(data, path="variance")

    assert data["consolidated"] is True
    assert data["currency"] == "USD"
    statuses = {p["period"]: p["status"] for p in data["periods"]}
    elapsed = [p for p in data["periods"] if p["status"] == "elapsed"]
    assert len(elapsed) == 1, statuses
    assert elapsed[0]["planned_outflow"] == "1000.00"
    assert elapsed[0]["actual_outflow"] == "1250.00"
    assert elapsed[0]["variance"] == "250.00"
    for money_key in ("planned_total", "actual_total", "variance_total"):
        _assert_money_str(data[money_key], money_key)
    assert data["planned_total"] == "1000.00"
    assert data["actual_total"] == "1250.00"
    assert data["variance_total"] == "250.00"
    assert data["elapsed_period_count"] == 1
    assert data["open_period_count"] == 1
    assert data["undated_payment_count"] == 0
    assert data["unconvertible_payment_count"] == 0


async def test_variance_excludes_and_counts_payments_it_cannot_place_or_express(realdb):
    """A completed payment with no `completed_at` cannot be dated, and one whose
    outflow is not establishable in the plan's currency must never be added at
    face value. Both are excluded from the totals and COUNTED."""
    a = realdb.info("a")
    mk_a = realdb.sessionmaker("a")
    today = datetime.now(UTC).date()
    past_anchor = today - timedelta(days=14)

    async with mk_a() as sa:
        ent = await _default_entity_id(sa, a.org_id)
        dated = await _seed_invoice(
            sa,
            a.org_id,
            ent,
            number="VAR-OK",
            vendor_name="OkCo",
            amount="100.00",
            due_date=past_anchor,
        )
        foreign = await _seed_invoice(
            sa,
            a.org_id,
            ent,
            number="VAR-EUR",
            vendor_name="EuroCo",
            amount="200.00",
            due_date=past_anchor,
            currency="EUR",
        )
        undated = await _seed_invoice(
            sa,
            a.org_id,
            ent,
            number="VAR-UNDATED",
            vendor_name="UndatedCo",
            amount="300.00",
            due_date=past_anchor,
        )
        await sa.flush()
        settled_at = datetime.combine(past_anchor, datetime.min.time(), tzinfo=UTC) + timedelta(
            hours=10
        )
        await _seed_completed_payment(sa, ent, dated.id, amount="100.00", completed_at=settled_at)
        await _seed_completed_payment(sa, ent, foreign.id, amount="200.00", completed_at=settled_at)
        await _seed_completed_payment(sa, ent, undated.id, amount="300.00", completed_at=None)
        row = await _save_snapshot(
            sa,
            a.org_id,
            entity_id=None,
            # A window that spans today, so the undated payment's `created_at`
            # (now) falls inside it and is counted rather than ignored.
            periods=[_week(past_anchor, "100.00"), _week(today, "0.00")],
            plan_date=past_anchor,
        )
        await sa.commit()
        plan_id = row.plan_id

    async with realdb.client(key="a", role="admin") as c:
        data = (await c.get(f"/api/cash-flow/plans/{plan_id}/variance")).json()

    assert data["actual_total"] == "100.00", "the EUR leg must not be added at face value"
    assert data["unconvertible_payment_count"] == 1
    assert data["undated_payment_count"] == 1


async def test_variance_runs_under_the_saved_plans_own_entity_scope(realdb):
    """An entity-scoped snapshot is scored against THAT entity's cash, even when
    the caller has a different entity selected — measuring one scope's
    projection against another's actuals is not a variance."""
    a = realdb.info("a")
    mk_a = realdb.sessionmaker("a")
    today = datetime.now(UTC).date()
    past_anchor = today - timedelta(days=14)

    async with mk_a() as sa:
        ent = await _default_entity_id(sa, a.org_id)
        other = await _seed_second_entity(sa, a.org_id, slug="sub3")
        await sa.flush()
        mine = await _seed_invoice(
            sa, a.org_id, ent, number="SCOPE-A", vendor_name="A", amount="100.00"
        )
        theirs = await _seed_invoice(
            sa, a.org_id, other.id, number="SCOPE-B", vendor_name="B", amount="900.00"
        )
        await sa.flush()
        settled_at = datetime.combine(past_anchor, datetime.min.time(), tzinfo=UTC) + timedelta(
            hours=8
        )
        await _seed_completed_payment(sa, ent, mine.id, amount="100.00", completed_at=settled_at)
        await _seed_completed_payment(
            sa, other.id, theirs.id, amount="900.00", completed_at=settled_at
        )
        row = await _save_snapshot(
            sa,
            a.org_id,
            entity_id=ent,
            periods=[_week(past_anchor, "100.00")],
            plan_date=past_anchor,
        )
        await sa.commit()
        plan_id = row.plan_id
        other_id = other.id

    async with realdb.client(key="a", role="admin") as c:
        data = (
            await c.get(
                f"/api/cash-flow/plans/{plan_id}/variance",
                headers={"X-Entity-ID": str(other_id)},
            )
        ).json()

    assert data["consolidated"] is False
    assert data["actual_total"] == "100.00", "the other entity's cash must not be scored here"
    assert data["variance_total"] == "0.00"


async def test_variance_reports_discount_follow_through(realdb):
    """The plan's own discount recommendations are part of what it promised, so
    the comparison reports how many were actually captured."""
    from app.models.cash_plan import CashPlan
    from app.models.discount import (
        OFFER_STATUS_ACCEPTED,
        OFFER_STATUS_OFFERED,
        DiscountOffer,
    )

    a = realdb.info("a")
    mk_a = realdb.sessionmaker("a")
    today = datetime.now(UTC).date()

    async with mk_a() as sa:
        ent = await _default_entity_id(sa, a.org_id)
        inv = await _seed_invoice(
            sa, a.org_id, ent, number="OFFER-1", vendor_name="OfferCo", amount="1000.00"
        )
        await sa.flush()
        accepted = DiscountOffer(
            id=uuid.uuid4(),
            organization_id=a.org_id,
            entity_id=ent,
            invoice_id=inv.id,
            base_amount=Decimal("1000.00"),
            currency="USD",
            tiers=[],
            status=OFFER_STATUS_ACCEPTED,
        )
        still_open = DiscountOffer(
            id=uuid.uuid4(),
            organization_id=a.org_id,
            entity_id=ent,
            invoice_id=inv.id,
            base_amount=Decimal("1000.00"),
            currency="USD",
            tiers=[],
            status=OFFER_STATUS_OFFERED,
        )
        sa.add_all([accepted, still_open])
        row = await _save_snapshot(
            sa,
            a.org_id,
            entity_id=None,
            periods=[_week(today, "0.00")],
            plan_date=today,
        )
        row.selected_offer_ids = [str(accepted.id), str(still_open.id), "not-a-uuid"]
        await sa.commit()
        plan_id = row.plan_id

    async with realdb.client(key="a", role="admin") as c:
        data = (await c.get(f"/api/cash-flow/plans/{plan_id}/variance")).json()

    assert data["selected_offer_count"] == 3
    assert data["captured_offer_count"] == 1

    mk_a = realdb.sessionmaker("a")
    async with mk_a() as sa:
        stored = (
            await sa.execute(select(CashPlan).where(CashPlan.plan_id == plan_id))
        ).scalar_one()
        assert stored.selected_offer_ids[2] == "not-a-uuid", "a bad id is skipped, not fatal"


async def test_save_then_variance_end_to_end(realdb):
    """The wiring: a plan saved today has no elapsed period yet, so every total
    is zero and the periods are labelled honestly rather than scored."""
    a = realdb.info("a")
    mk_a = realdb.sessionmaker("a")
    async with mk_a() as sa:
        ent = await _default_entity_id(sa, a.org_id)
        await _seed_invoice(
            sa,
            a.org_id,
            ent,
            number="E2E-1",
            vendor_name="E2ECo",
            amount="333.00",
            due_date=date.today() + timedelta(days=9),
        )
        await sa.commit()

    plan = await _propose_plan(realdb)
    async with realdb.client(key="a", role="admin") as c:
        assert (
            await c.post(f"/api/cash-flow/plans/{plan.plan_id}/save", json=_replay_body(plan))
        ).status_code == 201
        data = (await c.get(f"/api/cash-flow/plans/{plan.plan_id}/variance")).json()

    assert data["plan_id"] == plan.plan_id
    assert data["periods"], "the saved curve must be reported back"
    assert {p["status"] for p in data["periods"]} <= {"in_progress", "future"}
    assert data["elapsed_period_count"] == 0
    assert data["planned_total"] == "0"
    assert data["actual_total"] == "0"
