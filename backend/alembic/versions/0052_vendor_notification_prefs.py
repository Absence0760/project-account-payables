"""Vendor-user notification preferences: vendor_users.notification_prefs JSONB.

Adds a per-portal-user notification-preferences blob to ``vendor_users``,
mirroring ``users.notification_prefs`` (control plane) but vendor-scoped. The
supplier controls whether they get emailed when their invoices are paid /
rejected. Shape: ``{event_type: {"email": bool}}``; empty ``{}`` = use defaults
(opt-out, all channels on).

Revision ID: 0052_vendor_notification_prefs
Revises: 0051
Create Date: 2026-06-19

Tenant DB only (gated on the ``vendor_users`` table, so it no-ops on the control
plane and fans out to every tenant via ``scripts/migrate_all_tenants.py``).

Idempotent: ``ADD COLUMN IF NOT EXISTS`` with a ``'{}'`` server default and
``NOT NULL`` (the default backfills existing rows). The downgrade drops the
column ``IF EXISTS``. Mirrors the ``VendorUser`` model so fresh tenants built via
``create_all`` match a migrated tenant.
"""

from sqlalchemy import text

from alembic import op

revision = "0052_vendor_notification_prefs"
down_revision = "0051"
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


_UPGRADE = [
    "ALTER TABLE vendor_users ADD COLUMN IF NOT EXISTS notification_prefs "
    "jsonb NOT NULL DEFAULT '{}'::jsonb",
]

_DOWNGRADE = [
    "ALTER TABLE vendor_users DROP COLUMN IF EXISTS notification_prefs",
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
