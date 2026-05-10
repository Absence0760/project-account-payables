"""Credit memos.

Revision ID: 0012_credit_memos
Revises: 0011_vendor_1099_fields
Create Date: 2026-05-09

Tenant DB only. Adds ``credit_memos`` table for vendor-issued credits
that reduce payables. See ``backend/docs/credit-memos.md`` for the
lifecycle (open → applied → void).
"""

from sqlalchemy import text

from alembic import op

revision = "0012_credit_memos"
down_revision = "0011_vendor_1099_fields"
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
        CREATE TABLE IF NOT EXISTS credit_memos (
            id UUID PRIMARY KEY,
            memo_number VARCHAR(100) NOT NULL,
            vendor_id UUID NOT NULL REFERENCES vendors(id),
            invoice_id UUID REFERENCES invoices(id),
            amount NUMERIC(15, 2) NOT NULL,
            currency VARCHAR(3) NOT NULL DEFAULT 'USD',
            issued_date DATE,
            reason TEXT,
            status VARCHAR(20) NOT NULL DEFAULT 'open',
            applied_at TIMESTAMPTZ,
            applied_by VARCHAR(255),
            organization_id UUID NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_credit_memos_vendor_id ON credit_memos(vendor_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_credit_memos_invoice_id ON credit_memos(invoice_id)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_credit_memos_organization_id "
        "ON credit_memos(organization_id)"
    )


def downgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute("DROP TABLE IF EXISTS credit_memos")
