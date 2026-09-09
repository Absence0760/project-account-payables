"""All four budget dimensions are indexed on ``invoices`` — migration 0090.

``Budget.dimension`` has four legal values and
``services/budget_service._DIMENSION_MATCH_COLUMN`` attributes realised invoice
spend by an equality against the matching ``Invoice`` column. Two of those four
— ``department`` / ``project`` — have been indexed since migration 0044;
``cost_center`` and ``gl_account`` predate it and carried no index at all, so
the same query shape got an index scan on half the dimensions and a full seq
scan of ``invoices`` on the other half.

Measured before landing 0090 (local Postgres 16, single-entity tenant, warm
cache, median of 7 runs of the exact ``_actual_invoice_legs`` SQL; ``department``
is the already-indexed control and does not move):

    40 000 invoices   cost_center  7.7 ms → 1.8 ms   Seq Scan → Bitmap Index Scan
                      gl_account   6.5 ms → 1.5 ms   Seq Scan → Bitmap Index Scan
                      department   2.7 ms → 2.7 ms   unchanged (control)
    200 000 invoices  cost_center 15.9 ms → 7.4 ms   Seq Scan → Bitmap Index Scan
                      gl_account  14.9 ms → 6.7 ms   Seq Scan → Bitmap Index Scan
                      department   9.6 ms → 9.6 ms   unchanged (control)

What this file pins:

1. **The four dimensions stay symmetric.** Every column
   ``_DIMENSION_MATCH_COLUMN`` can select is indexed on the model, so a fifth
   dimension cannot be added onto an unindexed column without failing here.
2. **The migration DDL applies to a real tenant DB, is idempotent, and reverses.**
3. **The migration's index NAMES equal the ones ``create_all`` produces.** This
   is the real drift risk: tenants are provisioned two ways —
   ``tenant_provisioning._create_tenant_tables`` (``create_all``, fresh tenants)
   and Alembic (existing tenants) — and a hand-picked name in the migration
   would leave the two schemas permanently different.
4. **The index is usable for the production query's predicate.** Asserted under
   ``enable_seqscan = off``, which tests the SHAPE of the index against the
   shape of the predicate. The suite's tenants hold a handful of rows, where a
   seq scan is genuinely the cheaper plan — asserting the cost-based CHOICE
   there would be asserting a fiction, and the real-volume choice is the
   measurement recorded above.

Real-Postgres harness (`realdb`).
"""

from __future__ import annotations

import importlib.util
import inspect
import uuid
from pathlib import Path

import pytest
from sqlalchemy import or_, select, text
from sqlalchemy.dialects import postgresql

from app.models.invoice import Invoice
from app.models.procurement import Budget, BudgetDimension
from app.services.budget_service import (
    _DIMENSION_MATCH_COLUMN,
    REALISED_INVOICE_STATUSES,
    _invoice_scan_narrowing,
    _leg_columns,
)

TENANT = "a"

# The two indexes migration 0090 adds. Named for SQLAlchemy's default
# single-column convention (`ix_<table>_<column>`) — see test (3).
NEW_INDEXES = {"ix_invoices_cost_center", "ix_invoices_gl_account"}
# Already present since migration 0044; the control for "did we change these?".
EXISTING_INDEXES = {"ix_invoices_department", "ix_invoices_project"}

_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "0090_invoice_budget_dimension_indexes.py"
)


def _migration_module():
    """Import the migration for its SQL constants (it never runs ``op`` here)."""
    spec = importlib.util.spec_from_file_location("_mig_0090", _MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _invoice_index_names(session) -> set[str]:
    rows = await session.execute(
        text("SELECT indexname FROM pg_indexes WHERE tablename = 'invoices'")
    )
    return set(rows.scalars().all())


# ---------------------------------------------------------------------------
# 1. the four budget dimensions stay symmetric on the model
# ---------------------------------------------------------------------------


def test_every_budget_dimension_column_is_indexed():
    """A budget dimension whose Invoice column is unindexed seq-scans the whole
    invoice table on the `/budgets/check` path. All four must be indexed."""
    indexed = {col.name for index in Invoice.__table__.indexes for col in index.columns}
    for dimension in BudgetDimension:
        match_col = _DIMENSION_MATCH_COLUMN[dimension]
        assert match_col.key in indexed, (
            f"budget dimension {dimension.value!r} matches Invoice.{match_col.key}, "
            "which carries no index — see migration 0090"
        )


def test_dimension_match_map_covers_every_dimension():
    """Guards the loop above: a new dimension with no entry in the map would
    make it vacuously pass for that dimension."""
    assert set(_DIMENSION_MATCH_COLUMN) == set(BudgetDimension)


def test_model_declares_the_names_the_migration_creates():
    """The ORM and the migration must agree, or a create_all-provisioned tenant
    and a migrated one end up with differently-named indexes on the same
    columns."""
    declared = {index.name for index in Invoice.__table__.indexes}
    assert NEW_INDEXES <= declared
    assert EXISTING_INDEXES <= declared


# ---------------------------------------------------------------------------
# 2 + 3. the migration DDL, against a real tenant DB
# ---------------------------------------------------------------------------


def test_migration_is_tenant_gated_and_names_match_the_orm():
    """`invoices` is tenant-scoped, so the revision must no-op on the control
    plane — it gates on the table existing — and its CREATE statements must use
    the ORM's own index names."""
    mig = _migration_module()
    # The gate itself: `upgrade` returns early unless `invoices` exists, which
    # is what makes the revision a no-op on the control plane while still
    # advancing its head.
    gate_src = inspect.getsource(mig._is_tenant_db)
    assert "table_name = 'invoices'" in gate_src
    assert "if not _is_tenant_db():" in inspect.getsource(mig.upgrade)
    assert "if not _is_tenant_db():" in inspect.getsource(mig.downgrade)

    upgrade_sql = " ".join(mig._UPGRADE)
    downgrade_sql = " ".join(mig._DOWNGRADE)
    for name in NEW_INDEXES:
        assert f"CREATE INDEX IF NOT EXISTS {name} " in upgrade_sql
        assert f"DROP INDEX IF EXISTS {name}" in downgrade_sql
    # Fanned to tenants, not control-plane-only.
    assert "invoices" in mig.__doc__
    assert "TENANT DB ONLY" in mig.__doc__


async def test_migration_statements_apply_are_idempotent_and_reverse(realdb):
    """Run the migration's own SQL against a real tenant DB: down, up, up."""
    mig = _migration_module()
    mk = realdb.sessionmaker(TENANT)
    async with mk() as s:
        for statement in mig._DOWNGRADE:
            await s.execute(text(statement))
        await s.commit()
        after_downgrade = await _invoice_index_names(s)
        assert not (NEW_INDEXES & after_downgrade)
        # The downgrade touches ONLY what 0090 added.
        assert EXISTING_INDEXES <= after_downgrade

        for _ in range(2):  # idempotent: CREATE INDEX ... IF NOT EXISTS
            for statement in mig._UPGRADE:
                await s.execute(text(statement))
            await s.commit()

        after_upgrade = await _invoice_index_names(s)
    assert NEW_INDEXES <= after_upgrade
    assert EXISTING_INDEXES <= after_upgrade


async def test_provisioned_tenant_already_carries_both_indexes(realdb):
    """A tenant DB built by `create_all` (how fresh tenants are provisioned)
    must match a migrated one — the model carries `index=True` for exactly this
    reason."""
    async with realdb.sessionmaker(TENANT)() as s:
        names = await _invoice_index_names(s)
    assert NEW_INDEXES <= names, (
        "a freshly-provisioned tenant is missing indexes a migrated tenant has"
    )


# ---------------------------------------------------------------------------
# 4. the index is usable for the production query's predicate
# ---------------------------------------------------------------------------


def _actual_leg_query(dimension: BudgetDimension, budget: Budget):
    """The exact query `budget_service._actual_invoice_legs` builds for ONE
    budget. Rebuilt here rather than mocked so a change to the real leg's
    predicates fails this test instead of silently escaping it."""
    match_col = _DIMENSION_MATCH_COLUMN[dimension]
    total, excluded = _leg_columns(Invoice.amount, Invoice.currency)
    return (
        select(Budget.id, total, excluded)
        .select_from(Budget)
        .join(Invoice, match_col == Budget.dimension_value)
        .where(
            Budget.id.in_([budget.id]),
            *_invoice_scan_narrowing(match_col, [budget]),
            Invoice.status.in_(REALISED_INVOICE_STATUSES),
            or_(
                Budget.period_start.is_(None),
                Budget.period_end.is_(None),
                Invoice.invoice_date.between(Budget.period_start, Budget.period_end),
            ),
            or_(Budget.entity_id.is_(None), Invoice.entity_id == Budget.entity_id),
        )
        .group_by(Budget.id)
    )


@pytest.mark.parametrize(
    ("dimension", "index_name"),
    [
        (BudgetDimension.cost_center, "ix_invoices_cost_center"),
        (BudgetDimension.gl_account, "ix_invoices_gl_account"),
        (BudgetDimension.department, "ix_invoices_department"),
        (BudgetDimension.project, "ix_invoices_project"),
    ],
)
async def test_actual_leg_can_use_the_dimension_index(realdb, dimension, index_name):
    """Postgres can satisfy the real leg's `invoices` predicate from the
    dimension's index — the property migration 0090 exists to give
    `cost_center` / `gl_account`, asserted identically for the two that already
    had it.

    The probe is `_invoice_scan_narrowing`'s predicate ALONE, not the whole
    joined leg, and that is the point rather than a simplification. That helper
    exists precisely "so the `invoices` scan can use an index"; an index on the
    column it narrows is exactly the property under test, and it is the only
    index that can serve that predicate — so a plan naming it is a statement
    about the index's SHAPE, which is deterministic.

    EXPLAINing the whole joined leg was not. `enable_seqscan = off` removes the
    sequential path but leaves every OTHER index on `invoices`
    (`organization_id`, `status`, `entity_id`, `invoice_date`, …) as a candidate,
    so which one won was still a cost decision — the very "fiction" the file
    docstring says this test avoids. And the costs it decided on were degenerate:
    the probe `Budget` is deliberately never inserted, so the outer relation
    estimates at zero rows and every inner path costs about nothing. It happened
    to pick the dimension index on a developer's seeded database and a
    status-index-plus-Filter on CI's near-empty one — the same test, the same
    schema, opposite answers, and it went red on `main` twice for that reason.

    The predicate is built through `_invoice_scan_narrowing` rather than written
    out here, so it cannot drift from what production actually applies; a
    sibling assertion below pins that the leg still uses it."""
    budget = Budget(
        id=uuid.uuid4(),
        name="idx probe",
        dimension=dimension,
        dimension_value="PROBE-VALUE",
        amount=1000,
        currency="USD",
        organization_id=realdb.info(TENANT).org_id,
        # Entity-less on purpose: `_invoice_scan_narrowing` then returns the
        # dimension predicate alone, so the probe is single-column.
        entity_id=None,
    )
    match_col = _DIMENSION_MATCH_COLUMN[dimension]
    narrowing = _invoice_scan_narrowing(match_col, [budget])
    assert len(narrowing) == 1, "probe must be the dimension predicate alone"
    probe = select(Invoice.id).where(*narrowing)
    sql = str(probe.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    async with realdb.sessionmaker(TENANT)() as s:
        await s.execute(text("SET LOCAL enable_seqscan = off"))
        plan = "\n".join((await s.execute(text(f"EXPLAIN {sql}"))).scalars().all())
    assert index_name in plan, f"plan for {dimension.value} did not use {index_name}:\n{plan}"


@pytest.mark.parametrize("dimension", list(BudgetDimension))
def test_the_actual_leg_still_narrows_on_the_dimension_column(dimension):
    """The probe above is only meaningful while the production leg really does
    apply `_invoice_scan_narrowing` to `invoices`.

    Without this, narrowing could be dropped from `_actual_invoice_legs` and the
    index test would keep passing green against a predicate nothing issues.
    """
    budget = Budget(
        id=uuid.uuid4(),
        name="shape probe",
        dimension=dimension,
        dimension_value="PROBE-VALUE",
        amount=1000,
        currency="USD",
        organization_id=uuid.uuid4(),
        entity_id=None,
    )
    leg_sql = str(
        _actual_leg_query(dimension, budget).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    column = _DIMENSION_MATCH_COLUMN[dimension].key
    assert f"invoices.{column} IN " in leg_sql, (
        f"the actual leg no longer narrows invoices on {column}; the index probe "
        f"above is testing a predicate production does not issue:\n{leg_sql}"
    )
