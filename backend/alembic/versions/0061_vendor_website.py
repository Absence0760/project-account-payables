"""Vendor website column: vendors.website (tenant).

Adds a nullable ``website`` (``varchar(500)``) column to ``vendors`` so an AP
steward can APPLY an external-enrichment provider's website suggestion onto the
vendor through the audited apply path (``POST
/api/enrichment/vendors/{id}/apply``). Until now the enrich endpoint surfaced a
``website`` suggestion but the model had no column to land it on — see
``backend/docs/data-enrichment.md`` § Applying a suggestion.

Revision ID: 0061_vendor_website
Revises: 0060_po_expected_delivery_date
Create Date: 2026-06-20

TENANT DB ONLY: ``vendors`` is a tenant-scoped table (it does not exist on the
control plane ``account_payables`` DB). The upgrade is gated on the table
existing, so the same revision no-ops on the control DB and fans out to every
tenant DB via ``scripts/migrate_all_tenants.py`` (or
``FEOH_MIGRATE_TENANT=ap_<slug> alembic upgrade head`` for one). Fresh tenants get
the column from ``create_all`` in ``tenant_provisioning`` (it's on the model) —
this migration only backfills existing tenant DBs.

Idempotent + reversible: ``ADD COLUMN IF NOT EXISTS`` / ``DROP COLUMN IF EXISTS``.
The column is nullable (NULL = no website on file) — no default / backfill needed.
"""

from sqlalchemy import text

from alembic import op

revision = "0061_vendor_website"
down_revision = "0060_po_expected_delivery_date"
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


def upgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute("ALTER TABLE vendors ADD COLUMN IF NOT EXISTS website varchar(500)")


def downgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute("ALTER TABLE vendors DROP COLUMN IF EXISTS website")
