"""Supplier-portal user table.

Revision ID: 0009_vendor_users
Revises: 0008_approval_routing
Create Date: 2026-04-21

Tenant DB only — `vendor_users` holds supplier-portal credentials and sits
alongside `vendors`. The migration is guarded by `vendors` existence so
running it on the control plane is a no-op (control plane has no vendors).
"""

from sqlalchemy import text

from alembic import op

revision = "0009_vendor_users"
down_revision = "0008_approval_routing"
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
    if not _has_table("vendors"):
        return
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS vendor_users (
            id UUID PRIMARY KEY,
            vendor_id UUID NOT NULL REFERENCES vendors(id) ON DELETE CASCADE,
            email VARCHAR(320) NOT NULL UNIQUE,
            full_name VARCHAR(255) NOT NULL,
            hashed_password VARCHAR(255),
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            must_change_password BOOLEAN NOT NULL DEFAULT FALSE,
            last_login_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_vendor_users_vendor_id ON vendor_users(vendor_id)")


def downgrade() -> None:
    if not _has_table("vendor_users"):
        return
    op.execute("DROP TABLE IF EXISTS vendor_users")
