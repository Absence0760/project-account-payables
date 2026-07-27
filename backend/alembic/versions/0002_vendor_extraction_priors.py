"""Per-vendor correction cache table (tenant-scoped).

Revision ID: 0002_vendor_priors
Revises: 0001_signup
Create Date: 2026-04-19

Tenant-only migration. The `vendors` table lives in tenant DBs and not in
the control plane, so we gate the operation on its presence — when this
migration runs against the control plane (via the default
`alembic upgrade head`), it's a no-op; when it runs against a tenant DB
(via `FEOH_MIGRATE_TENANT=feoh_<slug> alembic upgrade head` or
`scripts/migrate_all_tenants.py`), it creates the table.
"""

from sqlalchemy import text

from alembic import op

revision = "0002_vendor_priors"
down_revision = "0001_signup"
branch_labels = None
depends_on = None


def _is_tenant_db() -> bool:
    """Probe for the `vendors` table — present in tenant DBs, absent in control."""
    bind = op.get_bind()
    result = bind.execute(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'vendors'"
        )
    ).scalar()
    return result is not None


def upgrade() -> None:
    if not _is_tenant_db():
        return

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS vendor_extraction_priors (
            id UUID PRIMARY KEY,
            vendor_id UUID NOT NULL REFERENCES vendors(id),
            field_name VARCHAR(60) NOT NULL,
            value TEXT NOT NULL,
            correction_count INTEGER NOT NULL DEFAULT 1,
            last_applied_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_vendor_priors_vendor_field UNIQUE (vendor_id, field_name)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_vendor_extraction_priors_vendor_id "
        "ON vendor_extraction_priors(vendor_id)"
    )


def downgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute("DROP TABLE IF EXISTS vendor_extraction_priors")
