"""Multi-currency reporting: materialized reporting-currency columns on invoices.

Revision ID: 0025_multicurrency
Revises: 0024_invoice_po_flip_unique
Create Date: 2026-06-12

Tenant DB only. Adds four nullable columns to ``invoices`` that persist the
conversion of ``amount`` into the org's reporting (base) currency, locking the
FX rate at materialization time so multi-currency analytics / dashboard rollups
never silently recompute historical conversions with today's rate (project
invariant: money is exact + auditable):

  - ``reporting_currency``  VARCHAR(3)   — the reporting currency the row was
    converted into (snapshotted; an org changing its reporting currency later
    doesn't rewrite already-materialized history until each invoice is touched).
  - ``reporting_amount``    NUMERIC(15, 2) — ``amount`` in ``reporting_currency``.
  - ``reporting_fx_rate``   NUMERIC(18, 8) — rate applied (invoice currency →
    reporting currency). 1 for same-currency rows.
  - ``reporting_fx_locked_at`` TIMESTAMPTZ — when the rate was locked.

All nullable — domestic same-currency rows don't strictly need them populated,
and existing rows stay NULL until next touched (the conversion service treats a
NULL reporting_amount as "not yet materialized" and falls back to the row's own
currency for that single row). No backfill: backfilling would require a
historical rate per invoice we don't have on hand.

Gated on presence of the ``invoices`` table so it's a no-op on the control-plane
DB; fans out to every tenant via ``scripts/migrate_all_tenants.py``. Fresh
tenants get the columns via ``create_all`` (the model carries them).
"""

from sqlalchemy import text

from alembic import op

revision = "0025_multicurrency"
down_revision = "0024_invoice_po_flip_unique"
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
        """
        ALTER TABLE invoices
            ADD COLUMN IF NOT EXISTS reporting_currency VARCHAR(3),
            ADD COLUMN IF NOT EXISTS reporting_amount NUMERIC(15, 2),
            ADD COLUMN IF NOT EXISTS reporting_fx_rate NUMERIC(18, 8),
            ADD COLUMN IF NOT EXISTS reporting_fx_locked_at TIMESTAMPTZ
        """
    )


def downgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute(
        """
        ALTER TABLE invoices
            DROP COLUMN IF EXISTS reporting_currency,
            DROP COLUMN IF EXISTS reporting_amount,
            DROP COLUMN IF EXISTS reporting_fx_rate,
            DROP COLUMN IF EXISTS reporting_fx_locked_at
        """
    )
