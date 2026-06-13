"""Spend-to-contract link: invoices.contract_id (tenant-scoped).

Revision ID: 0037_invoice_contract_link
Revises: 0036_contracts
Create Date: 2026-06-13

Adds the nullable ``invoices.contract_id`` FK that links an invoice to the
contract its spend draws down. Tenant DB only (gated on the ``contracts``
table created by 0036, so it no-ops on the control plane and on any DB that
never created contracts). Idempotent: ``ADD COLUMN IF NOT EXISTS`` +
``CREATE INDEX IF NOT EXISTS``. Mirrors ``app.models.invoice.Invoice``.
"""

from sqlalchemy import text

from alembic import op

revision = "0037_invoice_contract_link"
down_revision = "0036_contracts"
branch_labels = None
depends_on = None


def _has_contracts_table() -> bool:
    bind = op.get_bind()
    return (
        bind.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'contracts'"
            )
        ).scalar()
        is not None
    )


def upgrade() -> None:
    if not _has_contracts_table():
        return
    op.execute(
        "ALTER TABLE invoices ADD COLUMN IF NOT EXISTS contract_id uuid REFERENCES contracts(id)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_invoices_contract_id ON invoices (contract_id)")


def downgrade() -> None:
    if not _has_contracts_table():
        return
    op.execute("DROP INDEX IF EXISTS ix_invoices_contract_id")
    op.execute("ALTER TABLE invoices DROP COLUMN IF EXISTS contract_id")
