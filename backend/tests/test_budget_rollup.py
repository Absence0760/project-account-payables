"""Real-DB coverage for the org-wide budget-vs-actual rollup.

Covers ``GET /api/budgets/rollup`` (``api/budgets.py::budget_rollup``) and the
``services/budget_service.compute_budget_rollup`` fold it is built on, plus the
currency-mismatch disclosure (``excluded_row_count``) that both the rollup and
the per-budget ``/spend`` response now carry.

The rollup is the CFO's consolidated view: only the standalone ``/budgets`` page
and the per-budget ``/{id}/spend`` existed, so an org-wide allocated vs
committed vs actual meant opening budgets one at a time.

DO NOT run this file standalone in a concurrent build — the ``realdb`` fixture
truncates all tables sequentially. The orchestrator runs the suite at the end.
"""

import uuid
from decimal import Decimal

from app.models.entity import Entity
from app.models.invoice import Invoice
from app.models.procurement import Budget, PurchaseRequisition, RequisitionStatus


def _u() -> str:
    return uuid.uuid4().hex[:8]


async def _mk_budget(
    realdb,
    key="a",
    *,
    dimension="cost_center",
    dimension_value=None,
    amount="1000.00",
    period="2026",
    currency="USD",
    entity_id=None,
) -> tuple[uuid.UUID, str]:
    mk = realdb.sessionmaker(key)
    org_id = realdb.info(key).org_id
    bid = uuid.uuid4()
    value = dimension_value or f"CC-{_u()}"
    async with mk() as s:
        s.add(
            Budget(
                id=bid,
                name=f"Budget {_u()}",
                dimension=dimension,
                dimension_value=value,
                period=period,
                amount=Decimal(amount),
                currency=currency,
                entity_id=entity_id,
                organization_id=org_id,
            )
        )
        await s.commit()
    return bid, value


async def _mk_invoice(realdb, key="a", *, amount, cost_center, currency="USD", entity_id=None):
    mk = realdb.sessionmaker(key)
    org_id = realdb.info(key).org_id
    async with mk() as s:
        s.add(
            Invoice(
                id=uuid.uuid4(),
                invoice_number=f"INV-{_u()}",
                vendor_name="Acme Supply",
                amount=Decimal(amount),
                status="paid",
                cost_center=cost_center,
                currency=currency,
                entity_id=entity_id,
                organization_id=org_id,
            )
        )
        await s.commit()


async def _mk_requisition(realdb, key="a", *, budget_id, total, currency="USD"):
    mk = realdb.sessionmaker(key)
    org_id = realdb.info(key).org_id
    async with mk() as s:
        s.add(
            PurchaseRequisition(
                id=uuid.uuid4(),
                requisition_number=f"REQ-{_u()}",
                requester_user_id=uuid.uuid4(),
                budget_id=budget_id,
                total=Decimal(total),
                status=RequisitionStatus.approved,
                currency=currency,
                organization_id=org_id,
            )
        )
        await s.commit()


async def _mk_entity(realdb, key="a") -> uuid.UUID:
    mk = realdb.sessionmaker(key)
    org_id = realdb.info(key).org_id
    eid = uuid.uuid4()
    async with mk() as s:
        s.add(
            Entity(
                id=eid,
                name=f"Sub {_u()}",
                slug=f"sub-{_u()}",
                is_default=False,
                is_active=True,
                organization_id=org_id,
            )
        )
        await s.commit()
    return eid


def _by_ccy(body) -> dict:
    return {row["currency"]: row for row in body["by_currency"]}


# ---------------------------------------------------------------------------
# The rollup itself
# ---------------------------------------------------------------------------


async def test_rollup_sums_exactly_within_each_currency(realdb):
    """Allocated / committed / actual are exact decimal STRINGS, summed within a
    currency and never across one. Two USD budgets + one EUR budget must produce
    two independent rows, never one blended figure."""
    bid1, cc1 = await _mk_budget(realdb, amount="1000.00")
    bid2, cc2 = await _mk_budget(realdb, amount="2000.10")
    bid3, cc3 = await _mk_budget(realdb, amount="500.00", currency="EUR")

    await _mk_requisition(realdb, budget_id=bid1, total="100.00")
    await _mk_invoice(realdb, amount="250.05", cost_center=cc1)
    await _mk_invoice(realdb, amount="10.00", cost_center=cc2)
    await _mk_requisition(realdb, budget_id=bid3, total="50.00", currency="EUR")
    await _mk_invoice(realdb, amount="25.00", cost_center=cc3, currency="EUR")

    async with realdb.client(key="a", role="cfo") as c:
        res = await c.get("/api/budgets/rollup")
    assert res.status_code == 200
    body = res.json()
    assert body["budget_count"] == 3
    assert body["insufficient_data"] is False

    rows = _by_ccy(body)
    assert set(rows) == {"USD", "EUR"}

    usd = rows["USD"]
    assert usd["budget_count"] == 2
    assert usd["allocated"] == "3000.10"
    assert usd["committed"] == "100.00"
    assert usd["actual"] == "260.05"
    assert usd["remaining"] == "2640.05"
    # (100.00 + 260.05) / 3000.10 * 100
    assert usd["utilization_pct"] == "12.00"
    assert usd["over_budget_count"] == 0

    eur = rows["EUR"]
    assert eur["budget_count"] == 1
    assert eur["allocated"] == "500.00"
    assert eur["committed"] == "50.00"
    assert eur["actual"] == "25.00"
    assert eur["remaining"] == "425.00"

    # `bid2`'s value is only present via the USD row — the two currencies are
    # never added into one number anywhere in the payload.
    assert "3500.10" not in res.text


async def test_rollup_counts_over_budget_currencies(realdb):
    """A budget whose remaining went negative is counted per currency."""
    _bid, cc = await _mk_budget(realdb, amount="100.00")
    await _mk_invoice(realdb, amount="250.00", cost_center=cc)

    async with realdb.client(key="a", role="cfo") as c:
        body = (await c.get("/api/budgets/rollup")).json()
    usd = _by_ccy(body)["USD"]
    assert usd["remaining"] == "-150.00"
    assert usd["over_budget_count"] == 1


async def test_rollup_utilization_is_null_not_zero_when_nothing_allocated(realdb):
    """A currency allocating nothing has NO utilization — `null`, never
    `"0.00"`. "0% used" and "there is nothing to use" are opposite facts and 0%
    reads as the reassuring one (decisions §34)."""
    await _mk_budget(realdb, amount="0.00")

    async with realdb.client(key="a", role="cfo") as c:
        body = (await c.get("/api/budgets/rollup")).json()
    assert _by_ccy(body)["USD"]["utilization_pct"] is None


async def test_rollup_empty_set_reports_insufficient_data(realdb):
    """No budgets at all is a distinct state from a row of confident zeros."""
    async with realdb.client(key="a", role="cfo") as c:
        body = (await c.get("/api/budgets/rollup")).json()
    assert body["budget_count"] == 0
    assert body["by_currency"] == []
    assert body["insufficient_data"] is True
    assert body["excluded_row_count"] == 0


# ---------------------------------------------------------------------------
# The disclosure
# ---------------------------------------------------------------------------


async def test_rollup_discloses_currency_excluded_rows(realdb):
    """A foreign-currency invoice / requisition matching a budget is REFUSED by
    the spend legs (they never convert) — and counted, so the surface rendering
    the figure can say it is a floor rather than a complete picture."""
    bid, cc = await _mk_budget(realdb, amount="10000.00", currency="USD")
    await _mk_invoice(realdb, amount="300.00", cost_center=cc, currency="USD")
    await _mk_invoice(realdb, amount="999.00", cost_center=cc, currency="EUR")
    await _mk_requisition(realdb, budget_id=bid, total="700.00", currency="EUR")

    async with realdb.client(key="a", role="cfo") as c:
        body = (await c.get("/api/budgets/rollup")).json()
        spend = (await c.get(f"/api/budgets/{bid}/spend")).json()

    usd = _by_ccy(body)["USD"]
    assert usd["actual"] == "300.00"  # the EUR invoice is excluded, not added
    assert usd["committed"] == "0.00"  # the EUR requisition likewise
    assert usd["excluded_row_count"] == 2
    assert body["excluded_row_count"] == 2

    # The per-budget rollup carries the same disclosure.
    assert spend["actual"] == 300.0
    assert spend["excluded_row_count"] == 2


async def test_rollup_excluded_count_is_zero_on_a_clean_set(realdb):
    _bid, cc = await _mk_budget(realdb, amount="1000.00")
    await _mk_invoice(realdb, amount="10.00", cost_center=cc)
    async with realdb.client(key="a", role="cfo") as c:
        body = (await c.get("/api/budgets/rollup")).json()
    assert body["excluded_row_count"] == 0
    assert _by_ccy(body)["USD"]["excluded_row_count"] == 0


# ---------------------------------------------------------------------------
# Shares the list's filter builder + entity scope
# ---------------------------------------------------------------------------


async def test_rollup_honours_the_list_filters(realdb):
    """The rollup runs the SAME `_budget_list_filters` as `GET /api/budgets`, so
    it can never describe a different set than the table it sits above."""
    await _mk_budget(realdb, dimension="cost_center", amount="1000.00")
    await _mk_budget(realdb, dimension="project", amount="400.00", currency="EUR")

    async with realdb.client(key="a", role="cfo") as c:
        listed = (await c.get("/api/budgets", params={"dimension": "project"})).json()
        body = (await c.get("/api/budgets/rollup", params={"dimension": "project"})).json()

    assert listed["total"] == 1
    assert body["budget_count"] == 1
    assert [r["currency"] for r in body["by_currency"]] == ["EUR"]
    assert _by_ccy(body)["EUR"]["allocated"] == "400.00"


async def test_rollup_honours_the_search_filter(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    marker = f"Zeta{_u()}"
    async with mk() as s:
        s.add(
            Budget(
                id=uuid.uuid4(),
                name=marker,
                dimension="department",
                dimension_value="Engineering",
                period="2026",
                amount=Decimal("777.00"),
                currency="USD",
                organization_id=org_id,
            )
        )
        await s.commit()
    await _mk_budget(realdb, amount="1000.00")

    async with realdb.client(key="a", role="cfo") as c:
        body = (await c.get("/api/budgets/rollup", params={"search": marker})).json()
    assert body["budget_count"] == 1
    assert _by_ccy(body)["USD"]["allocated"] == "777.00"


async def test_rollup_is_entity_scoped(realdb):
    """`X-Entity-ID` narrows the rollup exactly like the list — a subsidiary's
    CFO never sees a sibling entity's allocation folded in."""
    other = await _mk_entity(realdb)
    await _mk_budget(realdb, amount="1000.00", entity_id=other)
    await _mk_budget(realdb, amount="55.00", entity_id=None)

    async with realdb.client(key="a", role="cfo") as c:
        scoped = (await c.get("/api/budgets/rollup", headers={"X-Entity-ID": str(other)})).json()
        unscoped = (await c.get("/api/budgets/rollup")).json()

    assert scoped["budget_count"] == 1
    assert _by_ccy(scoped)["USD"]["allocated"] == "1000.00"
    assert unscoped["budget_count"] == 2


# ---------------------------------------------------------------------------
# RBAC + tenant isolation
# ---------------------------------------------------------------------------


async def test_rollup_read_gate_matches_the_list(realdb):
    """Read is admin / ap_manager / cfo — the existing budgets read gate."""
    for role in ("admin", "ap_manager", "cfo"):
        async with realdb.client(key="a", role=role) as c:
            assert (await c.get("/api/budgets/rollup")).status_code == 200
    async with realdb.client(key="a", role="ap_clerk") as c:
        assert (await c.get("/api/budgets/rollup")).status_code == 403


async def test_rollup_is_tenant_isolated(realdb):
    await _mk_budget(realdb, "a", amount="1000.00")
    async with realdb.client(key="b", role="cfo") as c:
        body = (await c.get("/api/budgets/rollup")).json()
    assert body["budget_count"] == 0
    assert body["insufficient_data"] is True
