"""AI Cash-Flow Copilot — saved plans (`cash_plans` table).

Adds the tenant-scoped ``cash_plans`` table backing saved cash-flow plans and
plan-vs-actual: a FROZEN snapshot of one ``propose_payment_plan`` proposal —
the resolved parameters it was computed under, the period-by-period cash curve
it projected (money as exact decimal STRINGS in JSONB, never JSON numbers), and
the discount offers it selected. See ``app/models/cash_plan.py`` +
``docs/cash-flow-copilot.md`` §5/§12.

``entity_id`` NULL here means **consolidated** (a whole-group treasury plan),
not the "unstamped legacy row" NULL migration 0029 backfilled elsewhere — this
table is new, so nothing needs backfilling and no row can be unstamped.

Revision ID: 0087_cash_plans
Revises: 0086_invoice_reporting_src_ccy
Create Date: 2026-08-19

Tenant DB only (gated on the ``invoices`` table, so it no-ops on the control
plane and fans out to every tenant via ``scripts/migrate_all_tenants.py`` — or
``FEOH_MIGRATE_TENANT=feoh_<slug> alembic upgrade head`` for one). Fresh tenants
get the shape from ``create_all`` in
``tenant_provisioning._create_tenant_tables`` (it's on the model); this
migration only builds the table for existing tenants.

Idempotent + reversible: ``CREATE TABLE IF NOT EXISTS`` / ``CREATE [UNIQUE]
INDEX IF NOT EXISTS`` / ``DROP TABLE IF EXISTS``. Mirrors
``app.models.cash_plan`` exactly so a migrated tenant matches a
freshly-provisioned one. The ``entities`` FK exists by migration 0029.

The revision id is deliberately short: ``alembic_version.version_num`` is
VARCHAR(32) and migration 0086 shipped a 33-character id that aborted the whole
upgrade (see ``tests/test_alembic_revision_ids.py``).
"""

from sqlalchemy import text

from alembic import op

revision = "0087_cash_plans"
down_revision = "0086_invoice_reporting_src_ccy"
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


_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS cash_plans (
        id uuid PRIMARY KEY,
        organization_id uuid NOT NULL,
        plan_id varchar(64) NOT NULL,
        plan_date date NOT NULL,
        label varchar(200),
        granularity varchar(10) NOT NULL,
        horizon_days integer NOT NULL,
        min_balance_threshold numeric(15, 2),
        cash_budget numeric(15, 2),
        cost_of_capital_pct numeric(5, 2) NOT NULL,
        currency varchar(3) NOT NULL,
        opening_balance numeric(15, 2) NOT NULL,
        first_shortfall_period varchar(20),
        total_savings_selected numeric(15, 2) NOT NULL DEFAULT 0,
        total_outlay_selected numeric(15, 2) NOT NULL DEFAULT 0,
        unconverted_count integer NOT NULL DEFAULT 0,
        periods jsonb NOT NULL DEFAULT '[]'::jsonb,
        selected_offer_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
        unretimed_offer_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
        created_by_user_id uuid,
        entity_id uuid REFERENCES entities(id),
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_cash_plans_organization_id ON cash_plans (organization_id)",
    "CREATE INDEX IF NOT EXISTS ix_cash_plans_entity_id ON cash_plans (entity_id)",
    # The idempotency anchor for `POST /api/cash-flow/plans/{plan_id}/save`:
    # at most one snapshot per (tenant, deterministic plan id), so a retry
    # returns the existing row instead of storing a second snapshot taken
    # against newer data under the same id.
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_cash_plans_org_plan_id "
    "ON cash_plans (organization_id, plan_id)",
]


def upgrade() -> None:
    if not _is_tenant_db():
        return
    for stmt in _STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute("DROP TABLE IF EXISTS cash_plans")
