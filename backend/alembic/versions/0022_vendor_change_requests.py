"""Vendor self-service change-request staging table.

Revision ID: 0022_vendor_change_requests
Revises: 0021_scim_bearer_hash
Create Date: 2026-06-11

Tenant DB only — `vendor_change_requests` stages supplier-portal-initiated
changes to a vendor's sensitive fields (`bank_details`, `tax_id`) that must
NOT apply live. An AP admin approves/rejects each row; only on approval does
the change touch the `vendors` row. This staging step is the fraud control:
a vendor's bank change has zero effect on where money goes until AP signs off.

The migration is guarded by `vendors` existence so running it on the control
plane is a no-op (the control plane has no vendors). `proposed_value` JSONB
carries banking PII; it is never logged.
"""

from sqlalchemy import text

from alembic import op

revision = "0022_vendor_change_requests"
down_revision = "0021_scim_bearer_hash"
branch_labels = None
depends_on = None


def _is_tenant_db() -> bool:
    bind = op.get_bind()
    result = bind.execute(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = :t"
        ),
        {"t": "vendors"},
    ).scalar()
    return result is not None


def upgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS vendor_change_requests (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            vendor_id UUID NOT NULL REFERENCES vendors(id) ON DELETE CASCADE,
            organization_id UUID NOT NULL,
            requested_by_vendor_user_id UUID NOT NULL,
            change_type VARCHAR(30) NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            proposed_value JSONB NOT NULL,
            reviewed_by_user_id UUID,
            reviewed_at TIMESTAMPTZ,
            review_note TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_vendor_change_requests_vendor_id "
        "ON vendor_change_requests(vendor_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_vendor_change_requests_org_id "
        "ON vendor_change_requests(organization_id)"
    )
    # Partial index powers the admin "pending queue" scan without touching
    # the resolved (approved/rejected) history.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_vendor_change_requests_pending "
        "ON vendor_change_requests(status) WHERE status = 'pending'"
    )


def downgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute("DROP TABLE IF EXISTS vendor_change_requests")
