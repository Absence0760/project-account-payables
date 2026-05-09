"""Vendor 1099 / W-9 tax fields.

Revision ID: 0011_vendor_1099_fields
Revises: 0010_audit_log_shipping
Create Date: 2026-04-23

Tenant DB only. Adds columns on ``vendors`` so US tenants can collect +
track W-9 data and flag 1099-eligible vendors. See
``backend/docs/tax-1099.md`` for the reporting design.

Columns:
- ``tax_classification`` — IRS entity type (individual, llc_s_corp, ...).
- ``is_1099_eligible`` — flag set by AP when collecting W-9. Not
  auto-derived from classification because the $600 threshold +
  corporation exception requires tenant judgement.
- ``w9_received_date`` — date the tenant received + filed the form.
- ``w9_file_key`` — S3 key for the stored W-9 PDF (nullable).
- ``tin_verified_at`` — timestamp of last successful IRS TIN match
  (nullable, wired up when the Tax1099 integration lands).
"""

from sqlalchemy import text

from alembic import op

revision = "0011_vendor_1099_fields"
down_revision = "0010_audit_log_shipping"
branch_labels = None
depends_on = None


def _is_tenant_db() -> bool:
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
        "ALTER TABLE vendors "
        "ADD COLUMN IF NOT EXISTS tax_classification VARCHAR(50), "
        "ADD COLUMN IF NOT EXISTS is_1099_eligible BOOLEAN NOT NULL DEFAULT FALSE, "
        "ADD COLUMN IF NOT EXISTS w9_received_date DATE, "
        "ADD COLUMN IF NOT EXISTS w9_file_key VARCHAR(512), "
        "ADD COLUMN IF NOT EXISTS tin_verified_at TIMESTAMPTZ"
    )


def downgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute(
        "ALTER TABLE vendors "
        "DROP COLUMN IF EXISTS tax_classification, "
        "DROP COLUMN IF EXISTS is_1099_eligible, "
        "DROP COLUMN IF EXISTS w9_received_date, "
        "DROP COLUMN IF EXISTS w9_file_key, "
        "DROP COLUMN IF EXISTS tin_verified_at"
    )
