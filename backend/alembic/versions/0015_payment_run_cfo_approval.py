"""Payment run: CFO approval columns.

Revision ID: 0015_payment_run_cfo_approval
Revises: 0014_role_organization_scoped
Create Date: 2026-05-10

Tenant DB only. Adds columns on ``payment_runs`` so high-value runs
require an explicit CFO sign-off before they execute. The threshold
itself lives in `Organization.settings.payments.cfo_approval_above`
(controlled per-org from the UI).
"""

from sqlalchemy import text

from alembic import op

revision = "0015_payment_run_cfo_approval"
down_revision = "0014_role_organization_scoped"
branch_labels = None
depends_on = None


def _is_tenant_db() -> bool:
    bind = op.get_bind()
    return (
        bind.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'payment_runs'"
            )
        ).scalar()
        is not None
    )


def upgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute(
        "ALTER TABLE payment_runs "
        "ADD COLUMN IF NOT EXISTS requires_cfo_approval BOOLEAN NOT NULL DEFAULT FALSE, "
        "ADD COLUMN IF NOT EXISTS cfo_approved_by UUID, "
        "ADD COLUMN IF NOT EXISTS cfo_approved_at TIMESTAMPTZ"
    )


def downgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute(
        "ALTER TABLE payment_runs "
        "DROP COLUMN IF EXISTS requires_cfo_approval, "
        "DROP COLUMN IF EXISTS cfo_approved_by, "
        "DROP COLUMN IF EXISTS cfo_approved_at"
    )
