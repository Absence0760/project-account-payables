"""Add MFA (TOTP) columns to vendor_users (supplier-portal MFA).

Supplier-portal MFA (roadmap Priority 6) mirrors the employee MFA columns on
``User`` onto the tenant-scoped ``VendorUser``: a base32 TOTP secret, an enabled
flag (only true once the vendor verifies a code), and an enrolled-at timestamp.
See ``backend/docs/supplier-portal.md`` and ``docs/authentication.md`` § MFA.

Revision ID: 0053_vendor_mfa
Revises: 0052_vendor_notification_prefs
Create Date: 2026-06-19

Tenant DB only — ``vendor_users`` is a tenant table (gated on its existence, so
it no-ops on the control plane and fans out to every tenant via
``scripts/migrate_all_tenants.py``).

Idempotent: ``ADD COLUMN IF NOT EXISTS`` is a no-op when the column already
exists; the downgrade drops each column ``IF EXISTS``.
"""

from sqlalchemy import text

from alembic import op

revision = "0053_vendor_mfa"
down_revision = "0052_vendor_notification_prefs"
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
    op.execute("ALTER TABLE vendor_users ADD COLUMN IF NOT EXISTS mfa_secret VARCHAR(64)")
    op.execute(
        "ALTER TABLE vendor_users ADD COLUMN IF NOT EXISTS mfa_enabled BOOLEAN "
        "NOT NULL DEFAULT false"
    )
    op.execute("ALTER TABLE vendor_users ADD COLUMN IF NOT EXISTS mfa_enrolled_at TIMESTAMPTZ")


def downgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute("ALTER TABLE vendor_users DROP COLUMN IF EXISTS mfa_enrolled_at")
    op.execute("ALTER TABLE vendor_users DROP COLUMN IF EXISTS mfa_enabled")
    op.execute("ALTER TABLE vendor_users DROP COLUMN IF EXISTS mfa_secret")
