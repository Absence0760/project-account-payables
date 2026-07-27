"""VendorUser.organization_id — explicit tenant binding (tenant).

Adds a nullable ``organization_id`` column to ``vendor_users`` and backfills it
from the owning vendor's ``organization_id``. This makes the supplier-portal
user's tenant binding explicit and positively verifiable:
``get_current_vendor_user`` cross-checks it against the resolved tenant, so a
portal token can no longer authenticate against the wrong tenant on a (
astronomically unlikely) ``VendorUser.id`` UUID collision across tenant DBs, or
against a manually mis-provisioned row.

Revision ID: 0068_vendor_user_org
Revises: 0067_one_live_card_per_invoice
Create Date: 2026-07-01

TENANT DB ONLY: ``vendor_users`` is tenant-scoped (it does not exist on the
control-plane DB). The upgrade is gated on the table existing, so the revision
no-ops on the control DB and fans out to every tenant DB via
``scripts/migrate_all_tenants.py`` (or ``FEOH_MIGRATE_TENANT=feoh_<slug> alembic
upgrade head`` for one). Fresh tenants get the shape from ``create_all`` in
``tenant_provisioning`` (it's on the model) — this migration only adds +
backfills existing tenant DBs.

Idempotent + reversible: ``ADD COLUMN IF NOT EXISTS`` / ``DROP COLUMN IF
EXISTS``. Column stays nullable — the app tolerates NULL for un-backfilled
legacy rows and only enforces the cross-check when it is populated; every
creation site sets it going forward.
"""

from sqlalchemy import text

from alembic import op

revision = "0068_vendor_user_org"
down_revision = "0067_one_live_card_per_invoice"
branch_labels = None
depends_on = None


def _is_tenant_db() -> bool:
    bind = op.get_bind()
    return (
        bind.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'vendor_users'"
            )
        ).scalar()
        is not None
    )


def upgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute("ALTER TABLE vendor_users ADD COLUMN IF NOT EXISTS organization_id uuid")
    # Backfill from the owning vendor. Each vendor_users row points at exactly
    # one vendor, and that vendor carries the tenant's organization_id.
    op.execute(
        """
        UPDATE vendor_users AS vu
        SET organization_id = v.organization_id
        FROM vendors AS v
        WHERE v.id = vu.vendor_id
          AND vu.organization_id IS NULL
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_vendor_users_organization_id "
        "ON vendor_users (organization_id)"
    )


def downgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute("DROP INDEX IF EXISTS ix_vendor_users_organization_id")
    op.execute("ALTER TABLE vendor_users DROP COLUMN IF EXISTS organization_id")
