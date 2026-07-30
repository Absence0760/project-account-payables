"""AI Cash-Flow Copilot (Phases 1-2) — backend coverage.

Exercises the five finance-leader-only planning tools (`get_cashflow_forecast`,
`get_cash_position`, `run_payment_whatif`, `optimize_discount_capture`, and the
Phase 2 `propose_payment_plan`), the orchestrator's per-tool role gate +
copilot kill-switch in ``run_tool``, and the `/api/cash-flow/copilot` façade's
RBAC + 404 kill-switch, against the live per-process test tenant pair (the
shared ``realdb`` harness in ``conftest.py``).

The load-bearing invariants proven here (per ``docs/cash-flow-copilot.md`` §10):

  - **Money is exact** — every monetary field in a tool's ``model_dump(mode=
    "json")`` is an exact decimal STRING; there is no ``float`` anywhere in the
    return path (the tools deliberately do NOT inherit the analytics HTTP
    endpoints' ``float()`` chart coercion). Same inputs → byte-identical dump.
  - **Per-tool RBAC** — an ``ap_clerk`` invoking a copilot tool through the
    audited ``run_tool`` closure gets a clean refusal (``error`` set, ``result``
    None) — never data, never an exception; a finance leader gets data.
  - **Kill-switch** — with ``cashflow_copilot_enabled`` off, ``run_tool`` refuses
    the copilot tools and the façade routes 404.
  - **Optimizer parity** — ``optimize_discount_capture`` AND `propose_payment_plan`
    return the SAME selection + totals as ``POST /api/discounts/optimize`` for
    the same inputs (single source of truth; the plan never invents its own
    discount ranking).
  - **Draft-only boundary** — `propose_payment_plan` never mutates anything: no
    Payment/PaymentRun is created and no invoice status changes.
  - **Tenant isolation** — a tool bound to tenant A never surfaces tenant B's
    commitments.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

# ===========================================================================
# Helpers (mirror the realdb seeding patterns in test_assistant.py /
# test_discounts_api.py — do not invent a new fixture style).
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
    invoice_date=None,
    due_date=None,
    vendor_id=None,
):
    from app.models.invoice import Invoice

    inv = Invoice(
        id=uuid.uuid4(),
        organization_id=org_id,
        entity_id=entity_id,
        invoice_number=number,
        vendor_name=vendor_name,
        vendor_id=vendor_id,
        amount=Decimal(str(amount)),
        currency="USD",
        status=status,
        invoice_date=invoice_date or date.today(),
        due_date=due_date,
    )
    session.add(inv)
    return inv


async def _seed_schedule(session, invoice_id, *, due_date, discount_date, discount_percent):
    from app.models.payment import PaymentSchedule

    session.add(
        PaymentSchedule(
            id=uuid.uuid4(),
            invoice_id=invoice_id,
            due_date=due_date,
            discount_date=discount_date,
            discount_percent=Decimal(str(discount_percent)),
        )
    )


def _assert_no_float(obj, *, path="result"):
    """Recursively assert no ``float`` appears anywhere in a dumped result.

    Money must be an exact decimal string; ``bool``/``int`` (e.g. ``count``,
    ``below_threshold``), ``str`` and ``None`` are fine. A ``float`` at any depth
    means currency round-tripped through IEEE-754 — the exact bug §8 forbids.
    """
    if isinstance(obj, float):
        raise AssertionError(f"float found at {path}: {obj!r}")
    if isinstance(obj, dict):
        for k, v in obj.items():
            _assert_no_float(v, path=f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _assert_no_float(v, path=f"{path}[{i}]")


def _assert_money_str(value, path):
    """A money field must serialise as an exact decimal STRING, not a number."""
    assert isinstance(value, str), f"{path} should be an exact string, got {type(value)}: {value!r}"
    # Parses cleanly back to Decimal (no lossy float in the middle).
    Decimal(value)


# ===========================================================================
# 1. Money-exactness / determinism — the tool return path is float-free
# ===========================================================================


async def test_cashflow_forecast_money_is_exact_string_and_deterministic(realdb):
    """A seeded committed + pending mix yields exact-string totals (no float),
    the committed/pending split is exact, and two identical calls are
    byte-identical (deterministic under a stable clock)."""
    from app.services.assistant.tools.cashflow import get_cashflow_forecast
    from app.services.assistant.tools.schemas import CashflowForecastParams

    a = realdb.info("a")
    mk_a = realdb.sessionmaker("a")
    async with mk_a() as sa:
        ent = await _default_entity_id(sa, a.org_id)
        committed = await _seed_invoice(
            sa,
            a.org_id,
            ent,
            number="CF-COMMIT",
            vendor_name="CommitCo",
            amount="1234.56",
            status="approved",
            due_date=date.today() + timedelta(days=10),
        )
        await _seed_invoice(
            sa,
            a.org_id,
            ent,
            number="CF-PENDING",
            vendor_name="PendingCo",
            amount="500.00",
            status="pending",
            due_date=date.today() + timedelta(days=12),
        )
        await sa.flush()
        # A discount schedule makes the discount_eligible slice non-zero.
        await _seed_schedule(
            sa,
            committed.id,
            due_date=date.today() + timedelta(days=10),
            discount_date=date.today() + timedelta(days=5),
            discount_percent="2.00",
        )
        await sa.commit()

    ctrl_mk = realdb.control_sessionmaker()
    async with mk_a() as sa, ctrl_mk() as ctrl:
        res = await get_cashflow_forecast(
            sa,
            org_id=a.org_id,
            entity_id=None,
            current_user_id=a.users["admin"],
            control_db=ctrl,
            params=CashflowForecastParams(granularity="week", include_pending=True),
        )

    dumped = res.model_dump(mode="json")
    _assert_no_float(dumped)

    # Totals are exact strings and sum correctly.
    _assert_money_str(dumped["total_scheduled"], "total_scheduled")
    _assert_money_str(dumped["total_committed"], "total_committed")
    _assert_money_str(dumped["total_pending"], "total_pending")
    assert Decimal(dumped["total_committed"]) == Decimal("1234.56")
    assert Decimal(dumped["total_pending"]) == Decimal("500.00")
    assert Decimal(dumped["total_scheduled"]) == Decimal("1734.56")

    # Every per-period money field is an exact string; the discount-eligible
    # slice (from the schedule) is present and exact.
    total_discount_eligible = Decimal("0")
    for i, p in enumerate(dumped["periods"]):
        for field in ("scheduled", "committed", "pending", "discount_eligible"):
            _assert_money_str(p[field], f"periods[{i}].{field}")
        assert isinstance(p["count"], int)
        total_discount_eligible += Decimal(p["discount_eligible"])
    assert total_discount_eligible == Decimal("1234.56")

    # Determinism: a second identical call produces a byte-identical dump.
    async with mk_a() as sa, ctrl_mk() as ctrl:
        res2 = await get_cashflow_forecast(
            sa,
            org_id=a.org_id,
            entity_id=None,
            current_user_id=a.users["admin"],
            control_db=ctrl,
            params=CashflowForecastParams(granularity="week", include_pending=True),
        )
    assert res2.model_dump(mode="json") == dumped


async def test_cash_position_money_is_exact_string(realdb):
    """Running-balance periods + opening balance serialise as exact strings; the
    shortfall flag is a bool; no float in the return path."""
    from app.services.assistant.tools.cashflow import get_cash_position
    from app.services.assistant.tools.schemas import CashPositionParams

    a = realdb.info("a")
    mk_a = realdb.sessionmaker("a")
    async with mk_a() as sa:
        ent = await _default_entity_id(sa, a.org_id)
        await _seed_invoice(
            sa,
            a.org_id,
            ent,
            number="CP-1",
            vendor_name="OutflowCo",
            amount="4000.00",
            status="approved",
            due_date=date.today() + timedelta(days=7),
        )
        await sa.commit()

    ctrl_mk = realdb.control_sessionmaker()
    async with mk_a() as sa, ctrl_mk() as ctrl:
        res = await get_cash_position(
            sa,
            org_id=a.org_id,
            entity_id=None,
            current_user_id=a.users["admin"],
            control_db=ctrl,
            # Explicit opening balance keeps it deterministic (source="explicit",
            # no provider/settings lookup) and below-threshold detectable.
            params=CashPositionParams(
                granularity="week",
                opening_balance=Decimal("1000.00"),
                min_balance_threshold=Decimal("0.00"),
            ),
        )

    dumped = res.model_dump(mode="json")
    _assert_no_float(dumped)
    assert dumped["opening_balance_source"] == "explicit"
    _assert_money_str(dumped["opening_balance"], "opening_balance")
    _assert_money_str(dumped["min_balance_threshold"], "min_balance_threshold")
    assert Decimal(dumped["opening_balance"]) == Decimal("1000.00")
    assert dumped["periods"], "expected at least one running-balance period"
    for i, p in enumerate(dumped["periods"]):
        for field in ("opening", "outflow", "closing"):
            _assert_money_str(p[field], f"periods[{i}].{field}")
        assert isinstance(p["below_threshold"], bool)
    # 1000 opening − 4000 outflow → closing −3000 < 0 → first shortfall flagged.
    assert dumped["first_shortfall_period"] == dumped["periods"][0]["period"]
    assert dumped["periods"][0]["below_threshold"] is True
    assert Decimal(dumped["periods"][0]["closing"]) == Decimal("-3000.00")


async def test_payment_whatif_money_is_exact_string(realdb):
    """The early/on_time/late scenarios each serialise their outflow + captured
    discount + weighted days-to-pay as exact strings; no float in the path."""
    from app.services.assistant.tools.cashflow import run_payment_whatif
    from app.services.assistant.tools.schemas import PaymentWhatifParams

    a = realdb.info("a")
    mk_a = realdb.sessionmaker("a")
    async with mk_a() as sa:
        ent = await _default_entity_id(sa, a.org_id)
        inv = await _seed_invoice(
            sa,
            a.org_id,
            ent,
            number="WI-1",
            vendor_name="TimingCo",
            amount="1000.00",
            status="approved",
            due_date=date.today() + timedelta(days=20),
        )
        await sa.flush()
        # An early-pay discount so the `early` scenario captures a non-zero amount.
        await _seed_schedule(
            sa,
            inv.id,
            due_date=date.today() + timedelta(days=20),
            discount_date=date.today() + timedelta(days=5),
            discount_percent="2.00",
        )
        await sa.commit()

    ctrl_mk = realdb.control_sessionmaker()
    async with mk_a() as sa, ctrl_mk() as ctrl:
        res = await run_payment_whatif(
            sa,
            org_id=a.org_id,
            entity_id=None,
            current_user_id=a.users["admin"],
            control_db=ctrl,
            params=PaymentWhatifParams(granularity="week", grace_days=15),
        )

    dumped = res.model_dump(mode="json")
    _assert_no_float(dumped)
    scenarios = {s["scenario"]: s for s in dumped["scenarios"]}
    assert set(scenarios) == {"early", "on_time", "late"}
    for name, s in scenarios.items():
        for field in ("total_outflow", "discount_captured", "weighted_avg_days_to_pay"):
            _assert_money_str(s[field], f"{name}.{field}")
    # early captures 2% of 1000 → 20.00 and pays 980.00 net; on_time/late forfeit it.
    assert Decimal(scenarios["early"]["discount_captured"]) == Decimal("20.00")
    assert Decimal(scenarios["early"]["total_outflow"]) == Decimal("980.00")
    assert Decimal(scenarios["on_time"]["total_outflow"]) == Decimal("1000.00")
    assert Decimal(scenarios["late"]["discount_captured"]) == Decimal("0.00")


# ===========================================================================
# 2. Per-tool RBAC + kill-switch through the audited ``run_tool`` closure
# ===========================================================================


async def _build_copilot_run_tool(realdb, key, role, ctrl, tenant):
    """Construct the orchestrator's tenant-bound, audited ``run_tool`` closure
    for a given role, exactly as ``run_turn`` builds it — so the per-tool role
    gate + kill-switch are exercised through their real code path."""
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.models.assistant import Conversation
    from app.models.organization import Organization
    from app.models.user import User
    from app.services.assistant.orchestrator import _build_run_tool

    info = realdb.info(key)
    org = await ctrl.get(Organization, info.org_id)
    user = (
        await ctrl.execute(
            select(User).options(selectinload(User.roles)).where(User.id == info.users[role])
        )
    ).scalar_one()
    conv = Conversation(id=uuid.uuid4(), organization_id=info.org_id, user_id=user.id, title=None)
    tenant.add(conv)
    await tenant.flush()
    return _build_run_tool(
        control_db=ctrl,
        tenant_db=tenant,
        org=org,
        user=user,
        entity_id=None,
        conv=conv,
    )


@pytest.mark.parametrize(
    "tool_name",
    [
        "get_cashflow_forecast",
        "get_cash_position",
        "run_payment_whatif",
        "optimize_discount_capture",
        "propose_payment_plan",
    ],
)
async def test_run_tool_refuses_ap_clerk_for_every_copilot_tool(realdb, tool_name):
    """An ap_clerk asking any cash question gets a clean refusal tool result —
    ``error`` set, ``result`` None — never data and never a raised exception."""
    mk_a = realdb.sessionmaker("a")
    ctrl_mk = realdb.control_sessionmaker()
    async with ctrl_mk() as ctrl, mk_a() as tenant:
        run_tool = await _build_copilot_run_tool(realdb, "a", "ap_clerk", ctrl, tenant)
        inv = await run_tool(tool_name, {})

    assert inv.result is None, "a refused clerk must receive NO data"
    assert inv.error is not None
    assert "permission" in inv.error.lower()
    assert inv.tool == tool_name


async def test_run_tool_grants_finance_leader_exact_money(realdb):
    """A finance leader (admin) gets data through ``run_tool`` — and the result
    the closure returns (``model_dump(mode="json")``) carries exact-string money,
    not float."""
    a = realdb.info("a")
    mk_a = realdb.sessionmaker("a")
    async with mk_a() as sa:
        ent = await _default_entity_id(sa, a.org_id)
        await _seed_invoice(
            sa,
            a.org_id,
            ent,
            number="LEAD-1",
            vendor_name="LeaderCo",
            amount="742.10",
            status="approved",
            due_date=date.today() + timedelta(days=9),
        )
        await sa.commit()

    ctrl_mk = realdb.control_sessionmaker()
    async with ctrl_mk() as ctrl, mk_a() as tenant:
        run_tool = await _build_copilot_run_tool(realdb, "a", "admin", ctrl, tenant)
        inv = await run_tool("get_cashflow_forecast", {})

    assert inv.error is None
    assert inv.result is not None
    _assert_no_float(inv.result)
    _assert_money_str(inv.result["total_committed"], "total_committed")
    assert Decimal(inv.result["total_committed"]) == Decimal("742.10")


@pytest.mark.parametrize(
    "role",
    ["admin", "ap_manager", "cfo"],
)
async def test_run_tool_admits_all_three_finance_leader_roles(realdb, role):
    """Every finance-leader role — admin / ap_manager / cfo — is admitted by the
    per-tool gate (mirrors analytics' _CFO_ROLES)."""
    mk_a = realdb.sessionmaker("a")
    ctrl_mk = realdb.control_sessionmaker()
    async with ctrl_mk() as ctrl, mk_a() as tenant:
        run_tool = await _build_copilot_run_tool(realdb, "a", role, ctrl, tenant)
        inv = await run_tool("get_cashflow_forecast", {})
    assert inv.error is None, f"{role} should be admitted, got {inv.error!r}"
    assert inv.result is not None


@pytest.mark.parametrize(
    "tool_name",
    [
        "get_cashflow_forecast",
        "get_cash_position",
        "run_payment_whatif",
        "optimize_discount_capture",
        "propose_payment_plan",
    ],
)
async def test_run_tool_refuses_copilot_tools_when_disabled(realdb, monkeypatch, tool_name):
    """With the copilot kill-switch off, ``run_tool`` refuses every copilot tool
    even for a finance leader — a clean refusal, never data, never a 500."""
    from app.config import settings

    monkeypatch.setattr(settings, "cashflow_copilot_enabled", False)

    mk_a = realdb.sessionmaker("a")
    ctrl_mk = realdb.control_sessionmaker()
    async with ctrl_mk() as ctrl, mk_a() as tenant:
        run_tool = await _build_copilot_run_tool(realdb, "a", "admin", ctrl, tenant)
        inv = await run_tool(tool_name, {})
    assert inv.result is None
    assert inv.error is not None
    assert "not available" in inv.error.lower()


async def test_run_tool_still_allows_base_tools_when_copilot_disabled(realdb, monkeypatch):
    """The kill-switch is scoped to the four copilot tools — the five base
    assistant tools remain usable for a finance leader when it's off."""
    from app.config import settings

    monkeypatch.setattr(settings, "cashflow_copilot_enabled", False)

    a = realdb.info("a")
    mk_a = realdb.sessionmaker("a")
    async with mk_a() as sa:
        ent = await _default_entity_id(sa, a.org_id)
        await _seed_invoice(
            sa, a.org_id, ent, number="BASE-1", vendor_name="BaseCo", amount="10.00"
        )
        await sa.commit()

    ctrl_mk = realdb.control_sessionmaker()
    async with ctrl_mk() as ctrl, mk_a() as tenant:
        run_tool = await _build_copilot_run_tool(realdb, "a", "admin", ctrl, tenant)
        inv = await run_tool("list_invoices", {})
    assert inv.error is None
    assert inv.result is not None
    assert inv.result["total"] == 1


# ===========================================================================
# 3. Optimizer parity — the tool matches POST /api/discounts/optimize exactly
# ===========================================================================


async def test_optimizer_tool_matches_discounts_optimize_endpoint(realdb):
    """``optimize_discount_capture`` returns the SAME selection + totals as the
    canonical ``POST /api/discounts/optimize`` endpoint for identical inputs —
    the two share ``services.discount_optimizer.optimize`` as the single source
    of truth, so they must never diverge."""
    from app.services.assistant.tools.optimizer import optimize_discount_capture
    from app.services.assistant.tools.schemas import OptimizeDiscountsParams

    a = realdb.info("a")
    mk_a = realdb.sessionmaker("a")

    # Seed two invoices + create their discount offers through the real API so
    # the offers are structurally valid (tiers normalised, base_amount defaulted).
    async with mk_a() as sa:
        ent = await _default_entity_id(sa, a.org_id)
        inv_a = await _seed_invoice(
            sa,
            a.org_id,
            ent,
            number="OPT-A",
            vendor_name="AlphaCo",
            amount="1000.00",
            status="approved",
            due_date=date.today() + timedelta(days=30),
        )
        inv_b = await _seed_invoice(
            sa,
            a.org_id,
            ent,
            number="OPT-B",
            vendor_name="BetaCo",
            amount="5000.00",
            status="approved",
            due_date=date.today() + timedelta(days=30),
        )
        await sa.commit()
        inv_a_id, inv_b_id = str(inv_a.id), str(inv_b.id)

    tiers = [{"days": 5, "percent": "3.00"}, {"days": 10, "percent": "2.00"}]
    async with realdb.client(key="a", role="ap_manager") as c:
        for inv_id in (inv_a_id, inv_b_id):
            r = await c.post(
                "/api/discounts/offers",
                json={"scope": "invoice", "invoice_id": inv_id, "tiers": tiers},
            )
            assert r.status_code == 201, r.text
        # The canonical endpoint — no cash budget (select every worthwhile).
        ep = await c.post("/api/discounts/optimize", json={})
    assert ep.status_code == 200, ep.text
    ep_body = ep.json()

    # The tool, called directly against a fresh tenant + control session.
    ctrl_mk = realdb.control_sessionmaker()
    async with mk_a() as sa, ctrl_mk() as ctrl:
        tool_res = await optimize_discount_capture(
            sa,
            org_id=a.org_id,
            entity_id=None,
            current_user_id=a.users["admin"],
            control_db=ctrl,
            params=OptimizeDiscountsParams(),
        )

    # Same recommended set, same selection, same offer coverage.
    ep_recs = {r["offer_id"]: r["selected"] for r in ep_body["recommendations"]}
    tool_recs = {r.offer_id: r.selected for r in tool_res.recommendations}
    assert tool_recs == ep_recs
    ep_selected = {oid for oid, sel in ep_recs.items() if sel}
    tool_selected = {r.offer_id for r in tool_res.recommendations if r.selected}
    assert tool_selected == ep_selected
    assert tool_selected, "expected at least one worthwhile offer to be selected"

    # Same money math (endpoint serialises Decimal→number, tool keeps Decimal —
    # compare via Decimal so 20.0 and 20.00 are equal but exact).
    assert Decimal(str(ep_body["total_savings_selected"])) == tool_res.total_savings_selected
    assert Decimal(str(ep_body["total_savings_available"])) == tool_res.total_savings_available
    assert Decimal(str(ep_body["total_outlay_selected"])) == tool_res.total_outlay_selected
    assert Decimal(str(ep_body["cost_of_capital_pct"])) == tool_res.cost_of_capital_pct

    # And the tool's own dump is float-free with exact-string money.
    dumped = tool_res.model_dump(mode="json")
    _assert_no_float(dumped)
    _assert_money_str(dumped["total_savings_selected"], "total_savings_selected")


async def test_optimizer_tool_honours_cash_budget_like_endpoint(realdb):
    """With a cash budget, the tool's greedy selection matches the endpoint's for
    the same budget — proving the ceiling is applied identically on both paths."""
    from app.services.assistant.tools.optimizer import optimize_discount_capture
    from app.services.assistant.tools.schemas import OptimizeDiscountsParams

    a = realdb.info("a")
    mk_a = realdb.sessionmaker("a")
    async with mk_a() as sa:
        ent = await _default_entity_id(sa, a.org_id)
        inv_small = await _seed_invoice(
            sa,
            a.org_id,
            ent,
            number="BUD-SMALL",
            vendor_name="SmallCo",
            amount="500.00",
            status="approved",
            due_date=date.today() + timedelta(days=30),
        )
        inv_big = await _seed_invoice(
            sa,
            a.org_id,
            ent,
            number="BUD-BIG",
            vendor_name="BigCo",
            amount="10000.00",
            status="approved",
            due_date=date.today() + timedelta(days=30),
        )
        await sa.commit()
        small_id, big_id = str(inv_small.id), str(inv_big.id)

    tiers = [{"days": 5, "percent": "3.00"}]
    async with realdb.client(key="a", role="ap_manager") as c:
        for inv_id in (small_id, big_id):
            r = await c.post(
                "/api/discounts/offers",
                json={"scope": "invoice", "invoice_id": inv_id, "tiers": tiers},
            )
            assert r.status_code == 201, r.text
        # A budget that can only fund the small offer's outlay.
        ep = await c.post("/api/discounts/optimize", json={"cash_budget": "1000"})
    assert ep.status_code == 200, ep.text
    ep_recs = {r["offer_id"]: r["selected"] for r in ep.json()["recommendations"]}

    ctrl_mk = realdb.control_sessionmaker()
    async with mk_a() as sa, ctrl_mk() as ctrl:
        tool_res = await optimize_discount_capture(
            sa,
            org_id=a.org_id,
            entity_id=None,
            current_user_id=a.users["admin"],
            control_db=ctrl,
            params=OptimizeDiscountsParams(cash_budget=Decimal("1000")),
        )
    tool_recs = {r.offer_id: r.selected for r in tool_res.recommendations}
    assert tool_recs == ep_recs


# ===========================================================================
# 4. Tenant isolation — a tool bound to A never surfaces B's commitments
# ===========================================================================


async def test_forecast_tool_never_reads_other_tenant(realdb):
    """A forecast bound to tenant A never includes tenant B's commitments, even
    with a same-numbered invoice in B carrying a very different amount."""
    from app.services.assistant.tools.cashflow import get_cashflow_forecast
    from app.services.assistant.tools.schemas import CashflowForecastParams

    a, b = realdb.info("a"), realdb.info("b")
    mk_a, mk_b = realdb.sessionmaker("a"), realdb.sessionmaker("b")
    async with mk_a() as sa:
        ent_a = await _default_entity_id(sa, a.org_id)
        await _seed_invoice(
            sa,
            a.org_id,
            ent_a,
            number="ISO-1",
            vendor_name="TenantAVendor",
            amount="111.00",
            status="approved",
            due_date=date.today() + timedelta(days=8),
        )
        await sa.commit()
    async with mk_b() as sb:
        ent_b = await _default_entity_id(sb, b.org_id)
        await _seed_invoice(
            sb,
            b.org_id,
            ent_b,
            number="ISO-1",
            vendor_name="TenantBVendor",
            amount="999999.00",
            status="approved",
            due_date=date.today() + timedelta(days=8),
        )
        await sb.commit()

    ctrl_mk = realdb.control_sessionmaker()
    async with mk_a() as sa, ctrl_mk() as ctrl:
        res = await get_cashflow_forecast(
            sa,
            org_id=a.org_id,
            entity_id=None,
            current_user_id=a.users["admin"],
            control_db=ctrl,
            params=CashflowForecastParams(),
        )
    # A sees ONLY its own 111.00 commitment — never B's 999999.00.
    assert res.total_committed == Decimal("111.00")
    assert res.total_scheduled == Decimal("111.00")


async def test_optimizer_tool_never_reads_other_tenant(realdb):
    """The discount optimizer bound to A never ranks B's offers."""
    from app.services.assistant.tools.optimizer import optimize_discount_capture
    from app.services.assistant.tools.schemas import OptimizeDiscountsParams

    a, b = realdb.info("a"), realdb.info("b")
    mk_a, mk_b = realdb.sessionmaker("a"), realdb.sessionmaker("b")

    async def _seed_offer(mk, info, number):
        async with mk() as s:
            ent = await _default_entity_id(s, info.org_id)
            inv = await _seed_invoice(
                s,
                info.org_id,
                ent,
                number=number,
                vendor_name="X",
                amount="1000.00",
                status="approved",
                due_date=date.today() + timedelta(days=30),
            )
            await s.commit()
            return str(inv.id)

    inv_a_id = await _seed_offer(mk_a, a, "TENA-1")
    inv_b_id = await _seed_offer(mk_b, b, "TENB-1")

    tiers = [{"days": 5, "percent": "3.00"}]
    async with realdb.client(key="a", role="ap_manager") as ca:
        assert (
            await ca.post(
                "/api/discounts/offers",
                json={"scope": "invoice", "invoice_id": inv_a_id, "tiers": tiers},
            )
        ).status_code == 201
    async with realdb.client(key="b", role="ap_manager") as cb:
        b_offer = await cb.post(
            "/api/discounts/offers",
            json={"scope": "invoice", "invoice_id": inv_b_id, "tiers": tiers},
        )
        assert b_offer.status_code == 201
        b_offer_id = b_offer.json()["id"]

    ctrl_mk = realdb.control_sessionmaker()
    async with mk_a() as sa, ctrl_mk() as ctrl:
        res = await optimize_discount_capture(
            sa,
            org_id=a.org_id,
            entity_id=None,
            current_user_id=a.users["admin"],
            control_db=ctrl,
            params=OptimizeDiscountsParams(),
        )
    returned_ids = {r.offer_id for r in res.recommendations}
    assert b_offer_id not in returned_ids, "optimizer leaked tenant B's offer"
    # A's single offer is the only one ranked.
    assert len(res.recommendations) == 1


# ===========================================================================
# 5. propose_payment_plan (Phase 2) — plan assembly, re-timing, draft-only
# ===========================================================================


async def test_propose_payment_plan_money_exact_and_matches_optimizer(realdb):
    """The plan's discount selection is the SAME one `optimize_discount_capture`
    would pick for equivalent inputs (single source of truth — the plan never
    re-derives its own ranking), and every money field is an exact string."""
    from app.services.assistant.tools.cashflow import propose_payment_plan
    from app.services.assistant.tools.optimizer import optimize_discount_capture
    from app.services.assistant.tools.schemas import (
        OptimizeDiscountsParams,
        ProposePaymentPlanParams,
    )

    a = realdb.info("a")
    mk_a = realdb.sessionmaker("a")
    async with mk_a() as sa:
        ent = await _default_entity_id(sa, a.org_id)
        inv = await _seed_invoice(
            sa,
            a.org_id,
            ent,
            number="PLAN-1",
            vendor_name="PlanCo",
            amount="1000.00",
            status="approved",
            due_date=date.today() + timedelta(days=30),
        )
        await sa.commit()
        inv_id = str(inv.id)

    tiers = [{"days": 5, "percent": "3.00"}]
    async with realdb.client(key="a", role="ap_manager") as c:
        r = await c.post(
            "/api/discounts/offers",
            json={"scope": "invoice", "invoice_id": inv_id, "tiers": tiers},
        )
        assert r.status_code == 201, r.text

    ctrl_mk = realdb.control_sessionmaker()
    async with mk_a() as sa, ctrl_mk() as ctrl:
        plan = await propose_payment_plan(
            sa,
            org_id=a.org_id,
            entity_id=None,
            current_user_id=a.users["admin"],
            control_db=ctrl,
            params=ProposePaymentPlanParams(
                granularity="week", opening_balance=Decimal("5000.00")
            ),
        )
    async with mk_a() as sa, ctrl_mk() as ctrl:
        opt = await optimize_discount_capture(
            sa,
            org_id=a.org_id,
            entity_id=None,
            current_user_id=a.users["admin"],
            control_db=ctrl,
            params=OptimizeDiscountsParams(),
        )

    plan_selected = {r.offer_id for r in plan.discount_recommendations if r.selected}
    opt_selected = {r.offer_id for r in opt.recommendations if r.selected}
    assert plan_selected == opt_selected
    assert plan_selected, "expected the worthwhile 3% offer to be selected"
    assert plan.total_savings_selected == opt.total_savings_selected
    assert plan.total_outlay_selected == opt.total_outlay_selected

    dumped = plan.model_dump(mode="json")
    _assert_no_float(dumped)
    _assert_money_str(dumped["opening_balance"], "opening_balance")
    _assert_money_str(dumped["total_savings_selected"], "total_savings_selected")
    for i, p in enumerate(dumped["periods"]):
        for field in ("opening", "outflow", "closing"):
            _assert_money_str(p[field], f"periods[{i}].{field}")


async def test_propose_payment_plan_retimes_captured_discount_onto_pay_by(realdb):
    """A selected discount's invoice-scoped offer is removed from its due-date
    period and reappears, at its discounted outlay, in the pay_by period — the
    resulting curve reflects the plan's own capture decision, not the naive
    full-amount-on-due-date schedule."""
    from app.services.assistant.tools.cashflow import propose_payment_plan
    from app.services.assistant.tools.schemas import ProposePaymentPlanParams

    a = realdb.info("a")
    mk_a = realdb.sessionmaker("a")
    due = date.today() + timedelta(days=40)
    async with mk_a() as sa:
        ent = await _default_entity_id(sa, a.org_id)
        inv = await _seed_invoice(
            sa,
            a.org_id,
            ent,
            number="RETIME-1",
            vendor_name="RetimeCo",
            amount="1000.00",
            status="approved",
            due_date=due,
        )
        await sa.commit()
        inv_id = str(inv.id)

    # pay_by lands ~5 days out — a different week bucket than due (~40 days out).
    tiers = [{"days": 5, "percent": "5.00"}]
    async with realdb.client(key="a", role="ap_manager") as c:
        r = await c.post(
            "/api/discounts/offers",
            json={"scope": "invoice", "invoice_id": inv_id, "tiers": tiers},
        )
        assert r.status_code == 201, r.text

    ctrl_mk = realdb.control_sessionmaker()
    async with mk_a() as sa, ctrl_mk() as ctrl:
        plan = await propose_payment_plan(
            sa,
            org_id=a.org_id,
            entity_id=None,
            current_user_id=a.users["admin"],
            control_db=ctrl,
            params=ProposePaymentPlanParams(
                granularity="week", opening_balance=Decimal("5000.00")
            ),
        )

    selected = [r for r in plan.discount_recommendations if r.selected]
    assert len(selected) == 1
    assert not plan.unretimed_offer_ids, "an invoice-scoped offer should always re-time cleanly"

    # 1000 face − 50 savings (5%) = 950 outlay is what actually leaves the bank —
    # the full 1000 must NOT still be sitting on its original due-date period.
    total_outflow = sum((p.outflow for p in plan.periods), Decimal("0"))
    assert total_outflow == Decimal("950.00")


async def test_propose_payment_plan_never_double_counts_two_offers_on_one_invoice(realdb):
    """Two open, invoice-scoped `DiscountOffer`s on the SAME invoice can both be
    worthwhile and both get selected by the optimizer (it has no per-invoice
    dedupe) — but the plan must re-time the invoice's single row only ONCE.
    The lower-ranked offer still counts toward `total_savings_selected` (the
    optimizer's own total) but is flagged `unretimed_offer_ids` rather than
    doubling that invoice's outflow on the curve."""
    from app.services.assistant.tools.cashflow import propose_payment_plan
    from app.services.assistant.tools.schemas import ProposePaymentPlanParams

    a = realdb.info("a")
    mk_a = realdb.sessionmaker("a")
    due = date.today() + timedelta(days=30)
    async with mk_a() as sa:
        ent = await _default_entity_id(sa, a.org_id)
        inv = await _seed_invoice(
            sa,
            a.org_id,
            ent,
            number="DUP-1",
            vendor_name="DupCo",
            amount="1000.00",
            status="approved",
            due_date=due,
        )
        await sa.commit()
        inv_id = str(inv.id)

    # Two open offers on the same invoice — both worthwhile at 5% off in 5 days
    # of acceleration (~25 days to net due), so `cash_budget=None` selects both.
    async with realdb.client(key="a", role="ap_manager") as c:
        r1 = await c.post(
            "/api/discounts/offers",
            json={
                "scope": "invoice",
                "invoice_id": inv_id,
                "tiers": [{"days": 5, "percent": "4.00"}],
            },
        )
        assert r1.status_code == 201, r1.text
        r2 = await c.post(
            "/api/discounts/offers",
            json={
                "scope": "invoice",
                "invoice_id": inv_id,
                "tiers": [{"days": 5, "percent": "3.00"}],
            },
        )
        assert r2.status_code == 201, r2.text

    ctrl_mk = realdb.control_sessionmaker()
    async with mk_a() as sa, ctrl_mk() as ctrl:
        plan = await propose_payment_plan(
            sa,
            org_id=a.org_id,
            entity_id=None,
            current_user_id=a.users["admin"],
            control_db=ctrl,
            params=ProposePaymentPlanParams(
                granularity="week", opening_balance=Decimal("5000.00")
            ),
        )

    selected = [r for r in plan.discount_recommendations if r.selected]
    assert len(selected) == 2, "expected both offers on the invoice to be worthwhile and selected"
    # Exactly one is retimed (the higher-APR 4% offer, ranked first); the other
    # is honestly flagged rather than silently double-counted.
    assert len(plan.unretimed_offer_ids) == 1

    # The curve must reflect the invoice ONCE — at the retimed offer's discounted
    # outlay (1000 − 4% = 960), never the full 1000 AND never 1000+960 stacked.
    total_outflow = sum((p.outflow for p in plan.periods), Decimal("0"))
    assert total_outflow == Decimal("960.00"), (
        f"expected a single re-timed outlay of 960.00, got {total_outflow} "
        "(double-counted or mis-timed the shared invoice)"
    )


async def test_propose_payment_plan_flags_unretimed_vendor_scoped_offer(realdb):
    """A selected vendor-scoped (bulk) offer has no single invoice to re-time —
    it still counts toward `total_savings_selected` (the optimizer says it's
    worth capturing) but is honestly flagged in `unretimed_offer_ids` rather
    than silently misrepresenting the curve's precision."""
    from app.models.vendor import Vendor
    from app.services.assistant.tools.cashflow import propose_payment_plan
    from app.services.assistant.tools.schemas import ProposePaymentPlanParams

    a = realdb.info("a")
    mk_a = realdb.sessionmaker("a")
    async with mk_a() as sa:
        ent = await _default_entity_id(sa, a.org_id)
        vendor = Vendor(organization_id=a.org_id, name="BulkVendorCo", entity_id=ent)
        sa.add(vendor)
        await sa.flush()
        vendor_id = str(vendor.id)
        await _seed_invoice(
            sa,
            a.org_id,
            ent,
            number="BULK-1",
            vendor_name="BulkVendorCo",
            amount="1000.00",
            status="approved",
            due_date=date.today() + timedelta(days=30),
            vendor_id=vendor.id,
        )
        await sa.commit()

    future = (date.today() + timedelta(days=40)).isoformat()
    async with realdb.client(key="a", role="ap_manager") as c:
        r = await c.post(
            "/api/discounts/bulk-negotiate",
            json={
                "vendor_id": vendor_id,
                "tiers": [{"days": 7, "percent": "3.00"}],
                "valid_until": future,
            },
        )
        assert r.status_code == 201, r.text

    ctrl_mk = realdb.control_sessionmaker()
    async with mk_a() as sa, ctrl_mk() as ctrl:
        plan = await propose_payment_plan(
            sa,
            org_id=a.org_id,
            entity_id=None,
            current_user_id=a.users["admin"],
            control_db=ctrl,
            params=ProposePaymentPlanParams(
                granularity="week", opening_balance=Decimal("5000.00")
            ),
        )

    selected = [r for r in plan.discount_recommendations if r.selected]
    assert selected, "expected the vendor-scoped offer to be worthwhile and selected"
    assert plan.unretimed_offer_ids == [selected[0].offer_id]
    assert plan.total_savings_selected > Decimal("0")


async def test_propose_payment_plan_never_mutates_anything(realdb):
    """Draft-only boundary (docs/cash-flow-copilot.md §5): the plan is a pure
    read. No Payment/PaymentRun is created and the source invoice's status is
    untouched — proposing a plan can never move money."""
    from sqlalchemy import select

    from app.models.invoice import Invoice
    from app.models.payment import Payment, PaymentRun
    from app.services.assistant.tools.cashflow import propose_payment_plan
    from app.services.assistant.tools.schemas import ProposePaymentPlanParams

    a = realdb.info("a")
    mk_a = realdb.sessionmaker("a")
    async with mk_a() as sa:
        ent = await _default_entity_id(sa, a.org_id)
        inv = await _seed_invoice(
            sa,
            a.org_id,
            ent,
            number="NOMUTATE-1",
            vendor_name="NoMutateCo",
            amount="1000.00",
            status="approved",
            due_date=date.today() + timedelta(days=30),
        )
        await sa.commit()
        inv_id = inv.id
        inv_number = str(inv_id)

    tiers = [{"days": 5, "percent": "3.00"}]
    async with realdb.client(key="a", role="ap_manager") as c:
        r = await c.post(
            "/api/discounts/offers",
            json={"scope": "invoice", "invoice_id": inv_number, "tiers": tiers},
        )
        assert r.status_code == 201, r.text

    ctrl_mk = realdb.control_sessionmaker()
    async with mk_a() as sa, ctrl_mk() as ctrl:
        await propose_payment_plan(
            sa,
            org_id=a.org_id,
            entity_id=None,
            current_user_id=a.users["admin"],
            control_db=ctrl,
            params=ProposePaymentPlanParams(
                granularity="week", opening_balance=Decimal("5000.00")
            ),
        )

    async with mk_a() as sa:
        refreshed = await sa.get(Invoice, inv_id)
        assert refreshed.status == "approved"
        assert (await sa.execute(select(Payment))).first() is None
        assert (await sa.execute(select(PaymentRun))).first() is None


async def test_propose_payment_plan_never_reads_other_tenant(realdb):
    """A plan bound to tenant A never surfaces tenant B's commitments."""
    from app.services.assistant.tools.cashflow import propose_payment_plan
    from app.services.assistant.tools.schemas import ProposePaymentPlanParams

    a, b = realdb.info("a"), realdb.info("b")
    mk_a, mk_b = realdb.sessionmaker("a"), realdb.sessionmaker("b")
    async with mk_a() as sa:
        ent_a = await _default_entity_id(sa, a.org_id)
        await _seed_invoice(
            sa,
            a.org_id,
            ent_a,
            number="PLAN-ISO-1",
            vendor_name="TenantAVendor",
            amount="222.00",
            status="approved",
            due_date=date.today() + timedelta(days=8),
        )
        await sa.commit()
    async with mk_b() as sb:
        ent_b = await _default_entity_id(sb, b.org_id)
        await _seed_invoice(
            sb,
            b.org_id,
            ent_b,
            number="PLAN-ISO-1",
            vendor_name="TenantBVendor",
            amount="888888.00",
            status="approved",
            due_date=date.today() + timedelta(days=8),
        )
        await sb.commit()

    ctrl_mk = realdb.control_sessionmaker()
    async with mk_a() as sa, ctrl_mk() as ctrl:
        plan = await propose_payment_plan(
            sa,
            org_id=a.org_id,
            entity_id=None,
            current_user_id=a.users["admin"],
            control_db=ctrl,
            params=ProposePaymentPlanParams(
                granularity="week", opening_balance=Decimal("0")
            ),
        )
    total_outflow = sum((p.outflow for p in plan.periods), Decimal("0"))
    assert total_outflow == Decimal("222.00")


# ===========================================================================
# 6. Façade RBAC + kill-switch — POST /api/cash-flow/copilot
# ===========================================================================


async def test_facade_allows_finance_leader(realdb):
    """An admin reaches the copilot façade (200) and gets a tool invocation back
    — proving the finance-leader route is mounted and functional."""
    a = realdb.info("a")
    mk_a = realdb.sessionmaker("a")
    async with mk_a() as sa:
        ent = await _default_entity_id(sa, a.org_id)
        await _seed_invoice(
            sa, a.org_id, ent, number="FAC-1", vendor_name="FacadeCo", amount="321.00"
        )
        await sa.commit()

    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post("/api/cash-flow/copilot", json={"message": "list invoices"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["conversation_id"]
    assert body["tool_invocations"], "expected the façade to run a tool"


@pytest.mark.parametrize("role", ["admin", "ap_manager", "cfo"])
async def test_facade_allows_every_finance_leader_role(realdb, role):
    async with realdb.client(key="a", role=role) as c:
        resp = await c.post("/api/cash-flow/copilot", json={"message": "list invoices"})
    assert resp.status_code == 200, resp.text


async def test_facade_forbids_ap_clerk(realdb):
    """An ap_clerk is refused at the route (403) — the copilot reasons about the
    org's cash outflow plan, which excludes clerks (unlike the general
    assistant's four-role access)."""
    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await c.post("/api/cash-flow/copilot", json={"message": "list invoices"})
    assert resp.status_code == 403, resp.text


async def test_facade_requires_auth(realdb):
    """No credential → 401/403, never a 200 — auth before everything."""
    async with realdb.client(key="a", role=None) as c:
        resp = await c.post("/api/cash-flow/copilot", json={"message": "hi"})
    assert resp.status_code in (401, 403), resp.text


async def test_facade_404_when_copilot_disabled(realdb, monkeypatch):
    """With the kill-switch off the whole surface 404s — a finance leader who
    would otherwise be allowed gets a 404, so a disabled copilot is
    indistinguishable from an unmounted route."""
    from app.config import settings

    monkeypatch.setattr(settings, "cashflow_copilot_enabled", False)
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post("/api/cash-flow/copilot", json={"message": "list invoices"})
    assert resp.status_code == 404, resp.text


async def test_facade_stream_404_when_copilot_disabled(realdb, monkeypatch):
    """The streaming variant honours the same kill-switch."""
    from app.config import settings

    monkeypatch.setattr(settings, "cashflow_copilot_enabled", False)
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post("/api/cash-flow/copilot/stream", json={"message": "list invoices"})
    assert resp.status_code == 404, resp.text


async def test_facade_stream_forbids_ap_clerk(realdb):
    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await c.post("/api/cash-flow/copilot/stream", json={"message": "list invoices"})
    assert resp.status_code == 403, resp.text


async def test_facade_routes_plan_question_to_propose_payment_plan(realdb):
    """A 'propose a payment plan' ask reaches the façade, the mock adapter
    routes it to the new tool, and the tool invocation carries a plan result —
    proving the Phase 2 tool is wired end-to-end through the same route
    Phase 1's tools already use (no new route needed)."""
    a = realdb.info("a")
    mk_a = realdb.sessionmaker("a")
    async with mk_a() as sa:
        ent = await _default_entity_id(sa, a.org_id)
        await _seed_invoice(
            sa,
            a.org_id,
            ent,
            number="FAC-PLAN-1",
            vendor_name="FacadePlanCo",
            amount="654.00",
            status="approved",
            due_date=date.today() + timedelta(days=15),
        )
        await sa.commit()

    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post(
            "/api/cash-flow/copilot", json={"message": "propose a payment plan for this quarter"}
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    invocations = body["tool_invocations"]
    assert invocations and invocations[0]["tool"] == "propose_payment_plan"
    assert invocations[0]["error"] is None
    assert invocations[0]["result"]["periods"]
