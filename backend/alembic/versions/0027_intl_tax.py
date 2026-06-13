"""International-tax records — per-invoice VAT / GST / withholding figures.

Revision ID: 0027_intl_tax
Revises: 0026_us_tax_1099
Create Date: 2026-06-12

Tenant DB only. Adds `intl_tax_records` — one row per computed VAT / GST /
withholding figure, persisted so the per-period tax report can aggregate
"collected vs owed" without recomputing from drifting rates (the persisted
amount is the audit fact). Money columns are NUMERIC (never float) — the
*money is exact* invariant. No PII / banking data is stored on the row.

Gated on the presence of the `invoices` table so it's a no-op on the
control-plane DB; fans out to every tenant via
`scripts/migrate_all_tenants.py`. Idempotent DDL (`IF NOT EXISTS`).
"""

from sqlalchemy import text

from alembic import op

revision = "0027_intl_tax"
down_revision = "0026_us_tax_1099"
branch_labels = None
depends_on = None


def _is_tenant_db() -> bool:
    bind = op.get_bind()
    result = bind.execute(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'invoices'"
        )
    ).scalar()
    return result is not None


def upgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS intl_tax_records (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL,
            invoice_id UUID,
            kind VARCHAR(20) NOT NULL,
            country_code VARCHAR(2) NOT NULL,
            region VARCHAR(10),
            currency VARCHAR(3) NOT NULL,
            net_amount NUMERIC(15, 2) NOT NULL,
            tax_rate NUMERIC(7, 4) NOT NULL,
            tax_amount NUMERIC(15, 2) NOT NULL,
            settled_amount NUMERIC(15, 2),
            reverse_charge BOOLEAN NOT NULL DEFAULT false,
            components JSONB,
            tax_point_date DATE NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_intl_tax_records_invoice_id ON intl_tax_records (invoice_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_intl_tax_records_country_code "
        "ON intl_tax_records (country_code)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_intl_tax_records_tax_point_date "
        "ON intl_tax_records (tax_point_date)"
    )


def downgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute("DROP INDEX IF EXISTS ix_intl_tax_records_tax_point_date")
    op.execute("DROP INDEX IF EXISTS ix_intl_tax_records_country_code")
    op.execute("DROP INDEX IF EXISTS ix_intl_tax_records_invoice_id")
    op.execute("DROP TABLE IF EXISTS intl_tax_records")
