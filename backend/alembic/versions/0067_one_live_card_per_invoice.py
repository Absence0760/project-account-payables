"""One live virtual card per invoice: partial unique index (tenant).

Adds a partial unique index on ``virtual_cards(invoice_id)`` covering every
non-``cancelled`` card, so an invoice can have at most one LIVE card at a time.
This is the DB-level idempotency backstop for card issuance (the project's
money-idempotency invariant): a retried ``POST /api/cards/generate`` — or a
re-run of the payment-execution card path — can no longer mint a second
provider card for the same invoice. A cancelled card is excluded, so a
legitimate cancel-then-reissue still works (only one non-cancelled row).

Revision ID: 0067_one_live_card_per_invoice
Revises: 0066_vcr_ap_requester
Create Date: 2026-06-21

TENANT DB ONLY: ``virtual_cards`` is tenant-scoped. The upgrade is gated on the
table existing, so the revision no-ops on the control DB and fans out to every
tenant DB via ``scripts/migrate_all_tenants.py``. Fresh tenants get the index
from ``create_all`` in ``tenant_provisioning`` (it's declared on the model).

Idempotent: ``CREATE UNIQUE INDEX IF NOT EXISTS`` / ``DROP INDEX IF EXISTS``.
"""

from sqlalchemy import text

from alembic import op

revision = "0067_one_live_card_per_invoice"
down_revision = "0066_vcr_ap_requester"
branch_labels = None
depends_on = None


def _is_tenant_db() -> bool:
    bind = op.get_bind()
    return (
        bind.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'virtual_cards'"
            )
        ).scalar()
        is not None
    )


def upgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_virtual_cards_one_live_per_invoice "
        "ON virtual_cards (invoice_id) WHERE status <> 'cancelled'"
    )


def downgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute("DROP INDEX IF EXISTS uq_virtual_cards_one_live_per_invoice")
