"""Vendor risk & sanctions screening: denormalised screening-state +
risk-score columns on ``vendors`` (the ``sanctions_checks`` trail table
already exists from migration 0018).

Revision ID: 0042_vendor_risk_screening
Revises: 0041_procurement
Create Date: 2026-06-16

Tenant DB only (gated on the ``vendors`` table, so it no-ops on the control
plane and fans out to every tenant via ``scripts/migrate_all_tenants.py``).

Idempotent: ``ADD COLUMN IF NOT EXISTS`` + ``CREATE INDEX IF NOT EXISTS``.
Mirrors the new columns on ``app.models.vendor.Vendor`` exactly so a fresh
tenant built via ``tenant_provisioning._create_tenant_tables`` (``create_all``)
matches a migrated one. See ``docs/vendor-risk-screening.md``.
"""

from sqlalchemy import text

from alembic import op

revision = "0042_vendor_risk_screening"
down_revision = "0041_procurement"
branch_labels = None
depends_on = None


def _is_tenant_db() -> bool:
    bind = op.get_bind()
    return (
        bind.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'vendors'"
            )
        ).scalar()
        is not None
    )


_UPGRADE = [
    "ALTER TABLE vendors ADD COLUMN IF NOT EXISTS screening_status "
    "varchar(20) NOT NULL DEFAULT 'unscreened'",
    "ALTER TABLE vendors ADD COLUMN IF NOT EXISTS last_screened_at timestamptz",
    "ALTER TABLE vendors ADD COLUMN IF NOT EXISTS payments_blocked "
    "boolean NOT NULL DEFAULT false",
    "ALTER TABLE vendors ADD COLUMN IF NOT EXISTS payments_blocked_reason varchar(255)",
    "ALTER TABLE vendors ADD COLUMN IF NOT EXISTS payments_blocked_at timestamptz",
    "ALTER TABLE vendors ADD COLUMN IF NOT EXISTS risk_score numeric(5, 2)",
    "ALTER TABLE vendors ADD COLUMN IF NOT EXISTS risk_level "
    "varchar(20) NOT NULL DEFAULT 'unknown'",
    "ALTER TABLE vendors ADD COLUMN IF NOT EXISTS risk_factors jsonb",
    "ALTER TABLE vendors ADD COLUMN IF NOT EXISTS risk_scored_at timestamptz",
    # Review-queue / re-screen-sweep read patterns.
    "CREATE INDEX IF NOT EXISTS ix_vendors_screening_status "
    "ON vendors (screening_status)",
    "CREATE INDEX IF NOT EXISTS ix_vendors_last_screened_at "
    "ON vendors (last_screened_at)",
    # Partial index: the "blocked vendors" surface is small and hot.
    "CREATE INDEX IF NOT EXISTS ix_vendors_payments_blocked "
    "ON vendors (payments_blocked) WHERE payments_blocked",
]

_DOWNGRADE = [
    "DROP INDEX IF EXISTS ix_vendors_payments_blocked",
    "DROP INDEX IF EXISTS ix_vendors_last_screened_at",
    "DROP INDEX IF EXISTS ix_vendors_screening_status",
    "ALTER TABLE vendors DROP COLUMN IF EXISTS risk_scored_at",
    "ALTER TABLE vendors DROP COLUMN IF EXISTS risk_factors",
    "ALTER TABLE vendors DROP COLUMN IF EXISTS risk_level",
    "ALTER TABLE vendors DROP COLUMN IF EXISTS risk_score",
    "ALTER TABLE vendors DROP COLUMN IF EXISTS payments_blocked_at",
    "ALTER TABLE vendors DROP COLUMN IF EXISTS payments_blocked_reason",
    "ALTER TABLE vendors DROP COLUMN IF EXISTS payments_blocked",
    "ALTER TABLE vendors DROP COLUMN IF EXISTS last_screened_at",
    "ALTER TABLE vendors DROP COLUMN IF EXISTS screening_status",
]


def upgrade() -> None:
    if not _is_tenant_db():
        return
    bind = op.get_bind()
    for stmt in _UPGRADE:
        bind.execute(text(stmt))


def downgrade() -> None:
    if not _is_tenant_db():
        return
    bind = op.get_bind()
    for stmt in _DOWNGRADE:
        bind.execute(text(stmt))
