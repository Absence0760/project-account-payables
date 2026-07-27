"""One rebate per virtual card: unique index on card_rebates (tenant).

Adds a UNIQUE index on ``card_rebates(virtual_card_id)`` so a single-use virtual
card can yield at most one rebate. A single-use card settles exactly once → one
rebate; the card webhook already guards on ``card.status == "charged"`` +
event-id dedup, but this is the hard DB-level backstop against a double-rebate
under a race / Redis-outage (the project's money-idempotency invariant). The
settlement branch in ``api/cards.py`` inserts the rebate inside a savepoint, so a
duplicate is silently skipped without aborting the card completion + audit row.

Revision ID: 0069_card_rebate_unique
Revises: 0068_vendor_user_org
Create Date: 2026-07-01

TENANT DB ONLY: ``card_rebates`` is tenant-scoped (NOT in
``tenant_provisioning.CONTROL_TABLES``). The upgrade is gated on the table
existing, so the revision no-ops on the control DB and fans out to every tenant
DB via ``scripts/migrate_all_tenants.py`` (or ``FEOH_MIGRATE_TENANT=feoh_<slug>
alembic upgrade head`` for one). Fresh tenants get the index from ``create_all``
in ``tenant_provisioning`` (it's declared on the model).

Idempotent: ``CREATE UNIQUE INDEX IF NOT EXISTS`` / ``DROP INDEX IF EXISTS``.
"""

from sqlalchemy import text

from alembic import op

revision = "0069_card_rebate_unique"
down_revision = "0068_vendor_user_org"
branch_labels = None
depends_on = None


def _is_tenant_db() -> bool:
    bind = op.get_bind()
    return (
        bind.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'card_rebates'"
            )
        ).scalar()
        is not None
    )


def upgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_card_rebates_virtual_card "
        "ON card_rebates (virtual_card_id)"
    )


def downgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute("DROP INDEX IF EXISTS uq_card_rebates_virtual_card")
