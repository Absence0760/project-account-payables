"""Audit Log Summarization: add `meta` JSONB column to invoices.

Revision ID: 0022_invoice_meta_summary
Revises: 0021_scim_bearer_hash
Create Date: 2026-06-11

Tenant-only migration. Adds a free-form `meta` JSONB column to `invoices`,
which holds the cached audit-log summary under `meta["audit_summary"]`
(see services/audit_summary.py + backend/docs/audit-summary.md). Gated on
presence of the `invoices` table so it's a no-op on the control-plane DB.

NOTE (parallel-branch integration): four sibling features are being built on
parallel branches concurrently and add their own migrations off the same
`0021_scim_bearer_hash` head. At integration time this revision id / numbering
may collide and need renumbering, or an Alembic merge revision may be required
to unify the multiple heads. The DDL itself is idempotent (`IF NOT EXISTS`), so
re-running after a renumber is safe.
"""

from sqlalchemy import text

from alembic import op

revision = "0022_invoice_meta_summary"
down_revision = "0021_scim_bearer_hash"
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
    op.execute("ALTER TABLE invoices ADD COLUMN IF NOT EXISTS meta JSONB")


def downgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute("ALTER TABLE invoices DROP COLUMN IF EXISTS meta")
