"""PO-flip idempotency: partial unique index on invoices.reference_number.

Revision ID: 0024_invoice_po_flip_unique
Revises: 0023_merge_roadmap_fanout
Create Date: 2026-06-11

Tenant-only migration. Adds a partial UNIQUE index on `invoices.reference_number`
restricted to the supplier-portal PO-flip marker (`po-flip:<po_id>`), so a
concurrent double-flip of the same PO can never persist two invoices off one PO
(project invariant #2 — the flip seeds the AP→payment pipeline). The application
keeps its existing-invoice check as the fast path; this index is the durable
backstop the handler relies on (it catches the IntegrityError and returns the
idempotent response).

Partial predicate keeps the constraint from ever touching ordinary invoices'
`reference_number` values. Gated on presence of the `invoices` table so it's a
no-op on the control-plane DB; fans out to every tenant via
`scripts/migrate_all_tenants.py`.
"""

from sqlalchemy import text

from alembic import op

revision = "0024_invoice_po_flip_unique"
down_revision = "0023_merge_roadmap_fanout"
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
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_invoice_po_flip_ref "
        "ON invoices (reference_number) "
        "WHERE reference_number LIKE 'po-flip:%'"
    )


def downgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute("DROP INDEX IF EXISTS uq_invoice_po_flip_ref")
