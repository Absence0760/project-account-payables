"""Recurring / subscription invoice templates.

Adds the ``recurring_invoice_templates`` table (tenant-scoped) plus two link /
idempotency columns on ``invoices`` (``recurring_template_id`` +
``recurring_period_key``) and a partial unique index on that pair so the
generation sweep can never double-create an invoice for the same period.

Revision ID: 0046_recurring_invoices
Revises: 0045_punchout_sessions
Create Date: 2026-06-19

Tenant DB only (gated on the ``invoices`` table, so it no-ops on the control
plane and fans out to every tenant via ``scripts/migrate_all_tenants.py``).

Idempotent: ``CREATE TABLE IF NOT EXISTS`` + ``ADD COLUMN IF NOT EXISTS`` +
``CREATE INDEX IF NOT EXISTS``. Mirrors
``app.models.recurring_invoice.RecurringInvoiceTemplate`` and the new
``Invoice`` columns exactly so a fresh tenant built via
``tenant_provisioning._create_tenant_tables`` (``create_all``) matches a
migrated one. The FKs (vendors, entities, invoices) all exist by earlier
migrations.
"""

from sqlalchemy import text

from alembic import op

revision = "0046_recurring_invoices"
down_revision = "0045_punchout_sessions"
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
    CREATE TABLE IF NOT EXISTS recurring_invoice_templates (
        id uuid PRIMARY KEY,
        name varchar(200) NOT NULL,
        vendor_id uuid REFERENCES vendors(id),
        vendor_name varchar(255),
        description varchar(500),
        amount numeric(15, 2),
        currency varchar(3) NOT NULL DEFAULT 'USD',
        gl_account varchar(100),
        cost_center varchar(100),
        department varchar(100),
        project varchar(100),
        po_number varchar(100),
        payment_terms varchar(50),
        cadence varchar(20) NOT NULL DEFAULT 'monthly',
        day_of_period integer NOT NULL DEFAULT 1,
        start_date date NOT NULL,
        end_date date,
        next_run_on date,
        last_period_key varchar(40),
        last_generated_at timestamptz,
        generated_count integer NOT NULL DEFAULT 0,
        status varchar(20) NOT NULL DEFAULT 'active',
        variance_tolerance_pct numeric(6, 2),
        notes varchar(500),
        meta jsonb,
        organization_id uuid NOT NULL,
        entity_id uuid REFERENCES entities(id),
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_recurring_invoice_templates_organization_id "
    "ON recurring_invoice_templates (organization_id)",
    "CREATE INDEX IF NOT EXISTS ix_recurring_invoice_templates_vendor_id "
    "ON recurring_invoice_templates (vendor_id)",
    "CREATE INDEX IF NOT EXISTS ix_recurring_invoice_templates_entity_id "
    "ON recurring_invoice_templates (entity_id)",
    "CREATE INDEX IF NOT EXISTS ix_recurring_invoice_templates_status "
    "ON recurring_invoice_templates (status)",
    "CREATE INDEX IF NOT EXISTS ix_recurring_invoice_templates_next_run_on "
    "ON recurring_invoice_templates (next_run_on)",
    # Link + idempotency columns on invoices.
    "ALTER TABLE invoices ADD COLUMN IF NOT EXISTS recurring_template_id uuid "
    "REFERENCES recurring_invoice_templates(id)",
    "ALTER TABLE invoices ADD COLUMN IF NOT EXISTS recurring_period_key varchar(40)",
    "CREATE INDEX IF NOT EXISTS ix_invoices_recurring_template_id "
    "ON invoices (recurring_template_id)",
    # At most one invoice per (template, period). Partial predicate keeps it off
    # ordinary invoices.
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_invoice_recurring_period "
    "ON invoices (recurring_template_id, recurring_period_key) "
    "WHERE recurring_template_id IS NOT NULL",
]


def upgrade() -> None:
    if not _is_tenant_db():
        return
    for stmt in _STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute("DROP INDEX IF EXISTS uq_invoice_recurring_period")
    op.execute("DROP INDEX IF EXISTS ix_invoices_recurring_template_id")
    op.execute("ALTER TABLE invoices DROP COLUMN IF EXISTS recurring_period_key")
    op.execute("ALTER TABLE invoices DROP COLUMN IF EXISTS recurring_template_id")
    op.execute("DROP TABLE IF EXISTS recurring_invoice_templates")
