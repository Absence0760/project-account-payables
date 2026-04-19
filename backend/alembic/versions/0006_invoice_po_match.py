"""Add Invoice.po_match JSONB column.

Revision ID: 0006_po_match
Revises: 0005_mfa_user_columns
Create Date: 2026-04-19

Tenant-only — `invoices` lives in tenant DBs. Stores the latest 2/3-way PO
match result (status, variance, issues) computed by
`services.invoice_warnings.refresh_warnings`.
"""

from sqlalchemy import text

from alembic import op

revision = "0006_po_match"
down_revision = "0005_mfa_user_columns"
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
    op.execute("ALTER TABLE invoices ADD COLUMN IF NOT EXISTS po_match JSONB")


def downgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute("ALTER TABLE invoices DROP COLUMN IF EXISTS po_match")
