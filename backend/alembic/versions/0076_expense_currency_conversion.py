"""Locked FX conversion for expense lines + expense reports (tenant).

Issue #157: ``ExpenseReport.total_amount`` was a naive ``SUM(expenses.amount)``
across mixed currencies, and that contaminated total fed the CFO-threshold gate
— a $100 USD line plus a €200 EUR line on a USD report reported ``300.00 USD``.

Two locked-FX layers close it (mirroring ``invoices.reporting_*``, migration
0025, and ``payments.fx_rate`` / ``fx_locked_at``):

``expenses.converted_*``
    the line's ``amount`` expressed in the OWNING REPORT's currency, at a rate
    locked when the line is created/edited/attached. The report total sums these.

``expense_reports.reporting_*``
    the report's ``total_amount`` expressed in the ORG REPORTING currency, at a
    rate locked at SUBMIT time. The CFO threshold is a bare number in that
    currency, so this is what the gate compares against — otherwise a 4 900 EUR
    report slips under a 5 000 USD threshold.

Revision ID: 0076_expense_currency_conversion
Revises: 0075_intercompany_mirror_unique
Create Date: 2026-07-20

TENANT DB ONLY: ``expenses`` / ``expense_reports`` are tenant-scoped (they do
not exist on the control-plane DB). Both halves of the upgrade are gated on the
table existing, so the revision no-ops on the control DB and fans out to every
tenant DB via ``scripts/migrate_all_tenants.py`` (or
``FEOH_MIGRATE_TENANT=feoh_<slug> alembic upgrade head`` for one). Fresh tenants get
the shape from ``create_all`` in ``tenant_provisioning`` (the columns are on the
models) — this revision only backfills existing tenant DBs.

Idempotent + reversible: ``ADD COLUMN IF NOT EXISTS`` / ``DROP COLUMN IF
EXISTS``. All columns nullable with NO backfill, deliberately:

- An existing line whose currency already equals its report's currency needs no
  lock — the rollup falls back to its exact face ``amount`` (1:1, no FX).
- An existing FOREIGN-currency line legitimately has no locked rate, and
  inventing one here (at today's rate, inside a migration, with no adapter
  available) would fabricate history. It is instead counted as *unconverted*,
  excluded from the total, and blocks submission until it is re-attached (which
  locks a rate). Fail-closed by construction.
"""

from sqlalchemy import text

from alembic import op

revision = "0076_expense_currency_conversion"
down_revision = "0075_intercompany_mirror_unique"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    bind = op.get_bind()
    return (
        bind.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = :n"
            ),
            {"n": name},
        ).scalar()
        is not None
    )


_EXPENSE_COLUMNS = (
    ("converted_currency", "varchar(3)"),
    ("converted_amount", "numeric(15, 2)"),
    ("converted_fx_rate", "numeric(18, 8)"),
    ("converted_fx_locked_at", "timestamptz"),
)

_REPORT_COLUMNS = (
    ("reporting_currency", "varchar(3)"),
    ("reporting_amount", "numeric(15, 2)"),
    ("reporting_fx_rate", "numeric(18, 8)"),
    ("reporting_fx_locked_at", "timestamptz"),
)


def upgrade() -> None:
    if _has_table("expenses"):
        for name, coltype in _EXPENSE_COLUMNS:
            op.execute(f"ALTER TABLE expenses ADD COLUMN IF NOT EXISTS {name} {coltype}")
    if _has_table("expense_reports"):
        for name, coltype in _REPORT_COLUMNS:
            op.execute(f"ALTER TABLE expense_reports ADD COLUMN IF NOT EXISTS {name} {coltype}")


def downgrade() -> None:
    if _has_table("expenses"):
        for name, _ in _EXPENSE_COLUMNS:
            op.execute(f"ALTER TABLE expenses DROP COLUMN IF EXISTS {name}")
    if _has_table("expense_reports"):
        for name, _ in _REPORT_COLUMNS:
            op.execute(f"ALTER TABLE expense_reports DROP COLUMN IF EXISTS {name}")
