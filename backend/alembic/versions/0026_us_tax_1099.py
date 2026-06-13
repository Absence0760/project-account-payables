"""US 1099 tax: e-filing batch ledger.

Revision ID: 0026_us_tax_1099
Revises: 0025_multicurrency
Create Date: 2026-06-12

Tenant DB only. Adds ``tax_1099_filings`` — one row per 1099 e-filing batch
submission, keyed for idempotency on ``(organization_id, idempotency_key)``.
Filing a 1099 is a compliance-moving write (a duplicate IRS filing is a real
problem), so the unique constraint is what makes ``POST /api/tax/1099/file``
safe to retry: a repeat submit with the same key returns the stored
confirmation instead of re-filing.

The TIN-validation flow needs no schema change — it reuses the existing
``vendors.tin_verified_at`` column from migration 0011. The filings table
carries NO recipient TIN: only counts, the confirmation number, and a
redacted per-form result list. PII never lands here.

Idempotent DDL (``IF NOT EXISTS``) so re-running on a partially-migrated
tenant is safe.
"""

from sqlalchemy import text

from alembic import op

revision = "0026_us_tax_1099"
down_revision = "0025_multicurrency"
branch_labels = None
depends_on = None


def _is_tenant_db() -> bool:
    bind = op.get_bind()
    return (
        bind.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'vendors'"
            )
        ).scalar()
        is not None
    )


def upgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS tax_1099_filings (
            id UUID PRIMARY KEY,
            organization_id UUID NOT NULL,
            tax_year INTEGER NOT NULL,
            provider VARCHAR(50) NOT NULL,
            idempotency_key VARCHAR(120) NOT NULL,
            status VARCHAR(20) NOT NULL,
            confirmation_number VARCHAR(120),
            submitted_count INTEGER NOT NULL DEFAULT 0,
            accepted_count INTEGER NOT NULL DEFAULT 0,
            rejected_count INTEGER NOT NULL DEFAULT 0,
            submitted_by UUID,
            submitted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            result JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_tax_1099_filing_idempotency "
        "ON tax_1099_filings (organization_id, idempotency_key)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_tax_1099_filings_organization_id "
        "ON tax_1099_filings (organization_id)"
    )


def downgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute("DROP TABLE IF EXISTS tax_1099_filings")
