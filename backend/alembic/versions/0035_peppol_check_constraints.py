"""PEPPOL transmission CHECK constraints on direction + status (tenant-scoped).

Revision ID: 0035_peppol_check_constraints
Revises: 0034_peppol_transmissions
Create Date: 2026-06-13

Catch-up for tenants already migrated to 0034 (whose CREATE TABLE predated the
inline CHECK constraints): add ``ck_peppol_direction`` and ``ck_peppol_status``
so an invalid ``direction`` / ``status`` value can't slip past the partial
unique-index predicate (``WHERE status <> 'failed'``) and strand a live row.

Tenant DB only (gated on ``peppol_transmissions``, so it no-ops on the control
plane and on any tenant that never created the table). Idempotent: each
constraint is added only if a constraint of that name does not already exist —
so it is a no-op on a fresh tenant whose 0034 already created it inline, and on
re-run. Mirrors ``app.models.peppol_transmission.PeppolTransmission.__table_args__``.
"""

from sqlalchemy import text

from alembic import op

revision = "0035_peppol_check_constraints"
down_revision = "0034_peppol_transmissions"
branch_labels = None
depends_on = None


def _has_peppol_table() -> bool:
    bind = op.get_bind()
    return (
        bind.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'peppol_transmissions'"
            )
        ).scalar()
        is not None
    )


# Add each CHECK only when absent (Postgres lacks ADD CONSTRAINT IF NOT EXISTS).
_ADD = [
    (
        "ck_peppol_direction",
        "ALTER TABLE peppol_transmissions ADD CONSTRAINT ck_peppol_direction "
        "CHECK (direction IN ('outbound','inbound'))",
    ),
    (
        "ck_peppol_status",
        "ALTER TABLE peppol_transmissions ADD CONSTRAINT ck_peppol_status "
        "CHECK (status IN ('sending','sent','delivered','failed'))",
    ),
]


def upgrade() -> None:
    if not _has_peppol_table():
        return
    bind = op.get_bind()
    for name, ddl in _ADD:
        exists = bind.execute(
            text(
                "SELECT 1 FROM pg_constraint c "
                "JOIN pg_class t ON t.oid = c.conrelid "
                "WHERE t.relname = 'peppol_transmissions' AND c.conname = :name"
            ),
            {"name": name},
        ).scalar()
        if exists is None:
            op.execute(ddl)


def downgrade() -> None:
    if not _has_peppol_table():
        return
    op.execute("ALTER TABLE peppol_transmissions DROP CONSTRAINT IF EXISTS ck_peppol_status")
    op.execute("ALTER TABLE peppol_transmissions DROP CONSTRAINT IF EXISTS ck_peppol_direction")
