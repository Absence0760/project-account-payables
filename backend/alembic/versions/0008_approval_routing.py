"""Add approval routing columns — multi-level chains, delegation, segregation.

Revision ID: 0008_approval_routing
Revises: 0007_payment_lifecycle
Create Date: 2026-04-20

Tenant DB:
- invoices.uploaded_by_id — tracks who uploaded (for segregation of duties)
- workflow_steps.original_assigned_to — audit trail when delegation reassigns
- workflow_steps.approval_level — which chain level this step belongs to

Control plane:
- users.delegate_to_id — OOO proxy user
- users.delegate_until — when delegation expires
"""

from sqlalchemy import text

from alembic import op

revision = "0008_approval_routing"
down_revision = "0007_payment_lifecycle"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    result = bind.execute(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = :t"
        ),
        {"t": table_name},
    ).scalar()
    return result is not None


def upgrade() -> None:
    # Tenant DB columns
    if _has_table("invoices"):
        op.execute(
            "ALTER TABLE invoices ADD COLUMN IF NOT EXISTS uploaded_by_id UUID"
        )

    if _has_table("workflow_steps"):
        op.execute(
            "ALTER TABLE workflow_steps "
            "ADD COLUMN IF NOT EXISTS original_assigned_to UUID"
        )
        op.execute(
            "ALTER TABLE workflow_steps "
            "ADD COLUMN IF NOT EXISTS approval_level INTEGER"
        )

    # Control plane columns
    if _has_table("users"):
        op.execute(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS delegate_to_id UUID"
        )
        op.execute(
            "ALTER TABLE users "
            "ADD COLUMN IF NOT EXISTS delegate_until TIMESTAMPTZ"
        )


def downgrade() -> None:
    if _has_table("invoices"):
        op.execute("ALTER TABLE invoices DROP COLUMN IF EXISTS uploaded_by_id")

    if _has_table("workflow_steps"):
        op.execute(
            "ALTER TABLE workflow_steps "
            "DROP COLUMN IF EXISTS original_assigned_to"
        )
        op.execute(
            "ALTER TABLE workflow_steps DROP COLUMN IF EXISTS approval_level"
        )

    if _has_table("users"):
        op.execute("ALTER TABLE users DROP COLUMN IF EXISTS delegate_to_id")
        op.execute("ALTER TABLE users DROP COLUMN IF EXISTS delegate_until")
