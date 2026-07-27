"""PO expected-delivery date: purchase_orders.expected_delivery_date (tenant).

Adds a nullable ``expected_delivery_date`` (``date``) column to
``purchase_orders`` so the data-enrichment vendor performance score can compute a
**real** on-time-delivery sub-score: over the vendor's POs that carry an expected
date AND a goods receipt, the fraction received on or before that date
(``GoodsReceipt.received_date <= PurchaseOrder.expected_delivery_date``). Until
now on-time was N/A (or a weak invoice-due-date proxy) for lack of a PO-side
promised date — see ``backend/docs/data-enrichment.md`` § On-time delivery.

Revision ID: 0060_po_expected_delivery_date
Revises: 0059_email_locale_pref
Create Date: 2026-06-20

TENANT DB ONLY: ``purchase_orders`` is a tenant-scoped table (it does not exist
on the control plane ``account_payables`` DB). The upgrade is gated on the table
existing, so the same revision no-ops on the control DB and fans out to every
tenant DB via ``scripts/migrate_all_tenants.py`` (or
``FEOH_MIGRATE_TENANT=ap_<slug> alembic upgrade head`` for one). Fresh tenants get
the column from ``create_all`` in ``tenant_provisioning`` (it's on the model) —
this migration only backfills existing tenant DBs.

Idempotent + reversible: ``ADD COLUMN IF NOT EXISTS`` / ``DROP COLUMN IF EXISTS``.
The column is nullable (NULL = no promised date for that PO, excluded from the
on-time math) — no default / backfill needed.
"""

from sqlalchemy import text

from alembic import op

revision = "0060_po_expected_delivery_date"
down_revision = "0059_email_locale_pref"
branch_labels = None
depends_on = None


def _is_tenant_db() -> bool:
    bind = op.get_bind()
    return (
        bind.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'purchase_orders'"
            )
        ).scalar()
        is not None
    )


def upgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute("ALTER TABLE purchase_orders ADD COLUMN IF NOT EXISTS expected_delivery_date date")


def downgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute("ALTER TABLE purchase_orders DROP COLUMN IF EXISTS expected_delivery_date")
