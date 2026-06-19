"""Make exceptions.invoice_id nullable.

Nearly every exception is invoice-scoped, but a few fraud signals have no
invoice — notably a Positive Pay ``not_on_file`` return (a cheque the bank
cleared that we never issued). Dropping the NOT NULL lets those surface as a
standalone ``fraud_flag`` exception in the queue instead of being buried in a
JSON ``meta`` field.

Revision ID: 0049_exception_invoice_nullable
Revises: 0048_positive_pay
Create Date: 2026-06-19

Tenant DB only (gated on the ``invoices`` table, so it no-ops on the control
plane and fans out to every tenant via ``scripts/migrate_all_tenants.py``).

Idempotent: ``ALTER COLUMN ... DROP NOT NULL`` is a no-op when the column is
already nullable. The downgrade restores NOT NULL, which only succeeds if no
invoice-less rows exist (by design — you can't re-tighten the constraint while
standalone fraud exceptions are present).
"""

from sqlalchemy import text

from alembic import op

revision = "0049_exception_invoice_nullable"
down_revision = "0048_positive_pay"
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


def upgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute("ALTER TABLE exceptions ALTER COLUMN invoice_id DROP NOT NULL")


def downgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute("ALTER TABLE exceptions ALTER COLUMN invoice_id SET NOT NULL")
