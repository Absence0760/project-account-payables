"""Audit log shipping: add shipped_at column + partial index.

Revision ID: 0010_audit_log_shipping
Revises: 0009_vendor_users
Create Date: 2026-04-21

Tenant DB only. Adds a `shipped_at timestamptz NULL` column to `audit_log`
so the background shipper can mark rows as shipped to the centralized
WORM-compliant store (CloudWatch Logs + S3 Object Lock). Guarded by the
presence of `audit_log` so running against the control plane is a no-op.

Also adds a partial index on `shipped_at IS NULL` to keep the shipper's
"unshipped rows, oldest first" query cheap as the audit log grows.
"""

from sqlalchemy import text

from alembic import op

revision = "0010_audit_log_shipping"
down_revision = "0009_vendor_users"
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
    if not _has_table("audit_log"):
        return
    op.execute("ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS shipped_at TIMESTAMPTZ")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_audit_log_shipped_at_null "
        "ON audit_log(created_at) WHERE shipped_at IS NULL"
    )


def downgrade() -> None:
    if not _has_table("audit_log"):
        return
    op.execute("DROP INDEX IF EXISTS ix_audit_log_shipped_at_null")
    op.execute("ALTER TABLE audit_log DROP COLUMN IF EXISTS shipped_at")
