"""Index ``invoices.cost_center`` and ``invoices.gl_account`` — the two budget
dimensions that carried no index.

``Budget.dimension`` has four legal values, and
``services/budget_service._DIMENSION_MATCH_COLUMN`` attributes realised invoice
spend by an equality against the matching ``Invoice`` column. Two of those four
columns — ``department`` and ``project`` — have been indexed since migration
0044; ``cost_center`` and ``gl_account``, the two that predate it, never were.
So the same query shape got an index scan on two dimensions and a full seq scan
of ``invoices`` on the other two, for no reason a reader of the model could
infer.

**Measured, not assumed** (local Postgres 16, single-entity tenant, warm cache,
median of 7 runs of the exact ``_actual_invoice_legs`` SQL). ``department`` is
the control — already indexed, so it should not move:

| invoices | dimension     | before   | after   | plan before → after            |
|----------|---------------|----------|---------|--------------------------------|
| 40 000   | `cost_center` | 7.7 ms   | 1.8 ms  | Seq Scan → Bitmap Index Scan   |
| 40 000   | `gl_account`  | 6.5 ms   | 1.5 ms  | Seq Scan → Bitmap Index Scan   |
| 40 000   | `department`  | 2.7 ms   | 2.7 ms  | unchanged (control)            |
| 200 000  | `cost_center` | 15.9 ms  | 7.4 ms  | Seq Scan → Bitmap Index Scan   |
| 200 000  | `gl_account`  | 14.9 ms  | 6.7 ms  | Seq Scan → Bitmap Index Scan   |
| 200 000  | `department`  | 9.6 ms   | 9.6 ms  | unchanged (control)            |

The path that benefits is the SELECTIVE one — ``GET /budgets/{id}/spend`` and
``GET /budgets/check``, the latter sitting in front of every requisition
submit. The whole-tenant ``/budgets/rollup`` over half the distinct cost
centers measured 10.3 ms before and 9.1 ms after: at that selectivity a seq
scan is the right plan and the planner keeps choosing an equivalent one. The
index is not a rollup optimisation and is not claimed as one.

Cost: ~1.4 MB per index per 200k invoices — the same footprint
``ix_invoices_department`` / ``ix_invoices_project`` already pay, against a
39 MB table with 12 existing indexes.

Revision ID: 0090_invoice_budget_dim_idx
Revises: 0089_expense_pm_before_match
Create Date: 2026-09-05

TENANT DB ONLY: ``invoices`` is tenant-scoped (it is NOT in
``tenant_provisioning.CONTROL_TABLES``). The upgrade is gated on the table
existing, so the revision no-ops on the control plane and fans out to every
tenant DB via ``scripts/migrate_all_tenants.py`` (or
``FEOH_MIGRATE_TENANT=feoh_<slug> alembic upgrade head`` for one). Fresh tenants
get both indexes from ``create_all`` in ``tenant_provisioning`` — they are
declared ``index=True`` on ``app.models.invoice.Invoice``, exactly as
``department`` / ``project`` are, so a migrated tenant and a freshly-provisioned
one end up identical.

Idempotent + reversible: ``CREATE INDEX IF NOT EXISTS`` / ``DROP INDEX IF
EXISTS``. Naming follows SQLAlchemy's default single-column convention
(``ix_<table>_<column>``), which is what 0044 used and what ``create_all``
produces — a hand-picked name here would make the migrated and provisioned
schemas disagree.

See ``backend/docs/procurement-budgets.md`` § Index coverage on the four
dimensions.
"""

from sqlalchemy import text

from alembic import op

revision = "0090_invoice_budget_dim_idx"
down_revision = "0089_expense_pm_before_match"
branch_labels = None
depends_on = None


def _is_tenant_db() -> bool:
    bind = op.get_bind()
    return (
        bind.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'invoices'"
            )
        ).scalar()
        is not None
    )


_UPGRADE = [
    "CREATE INDEX IF NOT EXISTS ix_invoices_cost_center ON invoices (cost_center)",
    "CREATE INDEX IF NOT EXISTS ix_invoices_gl_account ON invoices (gl_account)",
]

_DOWNGRADE = [
    "DROP INDEX IF EXISTS ix_invoices_gl_account",
    "DROP INDEX IF EXISTS ix_invoices_cost_center",
]


def upgrade() -> None:
    if not _is_tenant_db():
        return
    for stmt in _UPGRADE:
        op.execute(stmt)


def downgrade() -> None:
    if not _is_tenant_db():
        return
    for stmt in _DOWNGRADE:
        op.execute(stmt)
