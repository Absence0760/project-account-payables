"""One live payment per invoice: partial unique index (tenant).

Adds a partial unique index on ``payments(invoice_id)`` covering every
NON-terminal payment (``status NOT IN ('voided', 'failed', 'cancelled')``), so
an invoice can have at most one LIVE payment at a time. This is the DB-level
idempotency backstop for the money-idempotency invariant: a retried /
double-clicked / concurrent ``POST /api/payments`` — or an overlapping payment
run — can no longer book a second full-amount payment for the same invoice.

Terminal states are excluded so a legitimate re-pay still works: a voided
payment hands its invoice back to ``approved`` to be paid again, and a failed /
cancelled attempt must not block a fresh one. Only one live (pending /
submitted / processing / pending_compliance / completed) row is allowed.

Revision ID: 0074_one_live_pay_per_invoice
Revises: 0073_po_matching_perf_indexes
Create Date: 2026-07-05

TENANT DB ONLY: ``payments`` is tenant-scoped. The upgrade is gated on the
table existing, so the revision no-ops on the control DB and fans out to every
tenant DB via ``scripts/migrate_all_tenants.py``. Fresh tenants get the index
from ``create_all`` in ``tenant_provisioning`` (it's declared on the model).

Idempotent: ``CREATE UNIQUE INDEX IF NOT EXISTS`` / ``DROP INDEX IF EXISTS``.
"""

from sqlalchemy import text

from alembic import op

revision = "0074_one_live_pay_per_invoice"
down_revision = "0073_po_matching_perf_indexes"
branch_labels = None
depends_on = None


def _is_tenant_db() -> bool:
    bind = op.get_bind()
    return (
        bind.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'payments'"
            )
        ).scalar()
        is not None
    )


def upgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_payments_one_live_per_invoice "
        "ON payments (invoice_id) "
        "WHERE status NOT IN ('voided', 'failed', 'cancelled')"
    )


def downgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute("DROP INDEX IF EXISTS uq_payments_one_live_per_invoice")
