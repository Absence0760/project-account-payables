"""One mirror per inter-company origin: partial unique index (tenant).

Adds a partial unique index on ``invoices(intercompany_mirror_id)`` covering
every non-NULL value, so an invoice can be named as the inter-company
mirror-partner of at most ONE other invoice.

``POST /api/invoices/{id}/route-intercompany`` generated the mirror payable
behind an in-memory ``intercompany_mirror_id IS NULL`` check with no row lock
and no constraint behind it. Two concurrent calls both observed NULL and each
INSERTed a live mirror payable under the counterparty entity — the orphaned
second one could then be approved and paid independently, i.e. a **double
liability**. The endpoint now row-locks the origin (``get_invoice_for_update``);
this index is the DB-level backstop that makes the duplicate impossible to
persist even if a future caller bypasses the lock (the losing INSERT raises
IntegrityError, which the handler turns into a clean 409).

The origin ↔ mirror link is bidirectional but 1:1 — the origin stores the
mirror's id and the mirror stores the origin's id, two DISTINCT values — so a
legitimate pair never collides. The partial predicate keeps ordinary invoices
(``intercompany_mirror_id IS NULL``, the overwhelming majority) entirely out of
the index, so NULLs never contend with each other.

Revision ID: 0075_intercompany_mirror_unique
Revises: 0074_one_live_pay_per_invoice
Create Date: 2026-07-20

TENANT DB ONLY: ``invoices`` is tenant-scoped. The upgrade is gated on the
table existing, so the revision no-ops on the control DB and fans out to every
tenant DB via ``scripts/migrate_all_tenants.py``. Fresh tenants get the index
from ``create_all`` in ``tenant_provisioning`` (it's declared on the model).

Idempotent: ``CREATE UNIQUE INDEX IF NOT EXISTS`` / ``DROP INDEX IF EXISTS``.
"""

from sqlalchemy import text

from alembic import op

revision = "0075_intercompany_mirror_unique"
down_revision = "0074_one_live_pay_per_invoice"
branch_labels = None
depends_on = None


def _is_tenant_db() -> bool:
    bind = op.get_bind()
    return (
        bind.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'invoices'"
            )
        ).scalar()
        is not None
    )


def upgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_invoice_intercompany_mirror "
        "ON invoices (intercompany_mirror_id) "
        "WHERE intercompany_mirror_id IS NOT NULL"
    )


def downgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute("DROP INDEX IF EXISTS uq_invoice_intercompany_mirror")
