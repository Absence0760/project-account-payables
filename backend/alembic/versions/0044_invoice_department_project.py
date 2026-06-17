"""Add ``department`` + ``project`` columns to ``invoices`` so procurement
budgets can match realised invoice spend on the ``department`` / ``project``
budget dimensions (previously only ``cost_center`` / ``gl_account`` matched —
actual read 0 for the other two).

Revision ID: 0044_invoice_department_project
Revises: 0043_dynamic_discounting
Create Date: 2026-06-17

Tenant DB only (gated on the ``invoices`` table, so it no-ops on the control
plane and fans out to every tenant via ``scripts/migrate_all_tenants.py``).

Idempotent: ``ADD COLUMN IF NOT EXISTS`` + ``CREATE INDEX IF NOT EXISTS``.
Mirrors ``app.models.invoice.Invoice`` (both columns ``index=True``) so a fresh
tenant built via ``tenant_provisioning._create_tenant_tables`` (``create_all``)
matches a migrated one. See ``backend/docs/procurement-budgets.md``.
"""

from sqlalchemy import text

from alembic import op

revision = "0044_invoice_department_project"
down_revision = "0043_dynamic_discounting"
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


_UPGRADE = [
    "ALTER TABLE invoices ADD COLUMN IF NOT EXISTS department varchar(100)",
    "ALTER TABLE invoices ADD COLUMN IF NOT EXISTS project varchar(100)",
    "CREATE INDEX IF NOT EXISTS ix_invoices_department ON invoices (department)",
    "CREATE INDEX IF NOT EXISTS ix_invoices_project ON invoices (project)",
]

_DOWNGRADE = [
    "DROP INDEX IF EXISTS ix_invoices_project",
    "DROP INDEX IF EXISTS ix_invoices_department",
    "ALTER TABLE invoices DROP COLUMN IF EXISTS project",
    "ALTER TABLE invoices DROP COLUMN IF EXISTS department",
]


def upgrade() -> None:
    if not _is_tenant_db():
        return
    for stmt in _UPGRADE:
        op.execute(stmt)


def downgrade() -> None:
    if not _is_tenant_db():
        return
    for stmt in _DOWNGRADE:
        op.execute(stmt)
