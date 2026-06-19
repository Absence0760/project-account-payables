"""Vendor statement reconciliation.

Adds the ``vendor_statement_reconciliations`` run table + its
``vendor_statement_recon_lines`` children (both tenant-scoped). A run
reconciles a supplier's statement of open items against our AP ledger; each
line is classified matched / amount_mismatch / missing_on_our_side /
missing_on_their_side.

Revision ID: 0047_vendor_statement_recon
Revises: 0046_recurring_invoices
Create Date: 2026-06-19

Tenant DB only (gated on the ``invoices`` table, so it no-ops on the control
plane and fans out to every tenant via ``scripts/migrate_all_tenants.py``).

Idempotent: ``CREATE TABLE IF NOT EXISTS`` + ``CREATE INDEX IF NOT EXISTS``.
Mirrors ``app.models.vendor_statement_recon`` exactly so a fresh tenant built
via ``tenant_provisioning._create_tenant_tables`` (``create_all``) matches a
migrated one. The FKs (vendors, entities, invoices) all exist by earlier
migrations.
"""

from sqlalchemy import text

from alembic import op

revision = "0047_vendor_statement_recon"
down_revision = "0046_recurring_invoices"
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
    CREATE TABLE IF NOT EXISTS vendor_statement_reconciliations (
        id uuid PRIMARY KEY,
        vendor_id uuid REFERENCES vendors(id),
        vendor_name varchar(255),
        statement_date date NOT NULL,
        statement_reference varchar(120),
        currency varchar(3) NOT NULL DEFAULT 'USD',
        source_format varchar(20) NOT NULL DEFAULT 'manual',
        file_key varchar(512),
        status varchar(20) NOT NULL DEFAULT 'open',
        statement_total numeric(18, 2),
        ledger_total numeric(18, 2),
        line_count integer NOT NULL DEFAULT 0,
        matched_count integer NOT NULL DEFAULT 0,
        amount_mismatch_count integer NOT NULL DEFAULT 0,
        missing_our_side_count integer NOT NULL DEFAULT 0,
        missing_their_side_count integer NOT NULL DEFAULT 0,
        notes varchar(500),
        created_by uuid,
        meta jsonb,
        organization_id uuid NOT NULL,
        entity_id uuid REFERENCES entities(id),
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_vendor_statement_reconciliations_organization_id "
    "ON vendor_statement_reconciliations (organization_id)",
    "CREATE INDEX IF NOT EXISTS ix_vendor_statement_reconciliations_vendor_id "
    "ON vendor_statement_reconciliations (vendor_id)",
    "CREATE INDEX IF NOT EXISTS ix_vendor_statement_reconciliations_entity_id "
    "ON vendor_statement_reconciliations (entity_id)",
    "CREATE INDEX IF NOT EXISTS ix_vendor_statement_reconciliations_status "
    "ON vendor_statement_reconciliations (status)",
    """
    CREATE TABLE IF NOT EXISTS vendor_statement_recon_lines (
        id uuid PRIMARY KEY,
        reconciliation_id uuid NOT NULL
            REFERENCES vendor_statement_reconciliations(id) ON DELETE CASCADE,
        organization_id uuid NOT NULL,
        statement_invoice_number varchar(100),
        statement_date date,
        statement_amount numeric(18, 2),
        statement_status varchar(40),
        classification varchar(30) NOT NULL,
        matched_invoice_id uuid REFERENCES invoices(id),
        ledger_amount numeric(18, 2),
        amount_difference numeric(18, 2),
        match_method varchar(40),
        resolution_status varchar(20) NOT NULL DEFAULT 'unresolved',
        resolution_note varchar(500),
        resolved_by uuid,
        resolved_at timestamptz,
        raw jsonb,
        entity_id uuid REFERENCES entities(id),
        created_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_vendor_statement_recon_lines_reconciliation_id "
    "ON vendor_statement_recon_lines (reconciliation_id)",
    "CREATE INDEX IF NOT EXISTS ix_vendor_statement_recon_lines_classification "
    "ON vendor_statement_recon_lines (classification)",
    "CREATE INDEX IF NOT EXISTS ix_vendor_statement_recon_lines_entity_id "
    "ON vendor_statement_recon_lines (entity_id)",
]


def upgrade() -> None:
    if not _is_tenant_db():
        return
    for stmt in _STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute("DROP TABLE IF EXISTS vendor_statement_recon_lines")
    op.execute("DROP TABLE IF EXISTS vendor_statement_reconciliations")
