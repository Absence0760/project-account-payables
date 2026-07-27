"""Threshold currency for expense policies (tenant).

``services/expense_policy.evaluate_expense`` compared ``category_limit``,
``per_diem_amount``, ``requires_receipt_above`` and ``requires_preapproval_above``
against ``expenses.amount`` **currency-blind** — a €200 EUR expense was judged
against a USD 100 category limit as bare numbers. ``receipt_required`` is a
BLOCKING code, so this was not merely advisory: a policy could block, or fail to
block, on a comparison that meant nothing.

The data model could not express the fix: ``expense_policies`` carried a
``per_diem_currency`` for one threshold and no currency at all for the other
three. This revision adds the missing unit.

``expense_policies.threshold_currency``
    the currency EVERY money threshold on the row is denominated in. The engine
    expresses the expense in this currency (reusing the rate #157 already locked
    onto ``expenses.converted_*``) before comparing, and when it cannot, every
    rule fails closed.

Revision ID: 0077_expense_policy_currency
Revises: 0076_expense_currency_conversion
Create Date: 2026-07-20

(The revision id is kept under 32 chars — the width of
``alembic_version.version_num`` in this project's DBs.)

TENANT DB ONLY: ``expense_policies`` is tenant-scoped (it does not exist on the
control-plane DB). The upgrade is gated on the table existing, so the revision
no-ops on the control DB and fans out to every tenant DB via
``scripts/migrate_all_tenants.py`` (or ``FEOH_MIGRATE_TENANT=ap_<slug> alembic
upgrade head`` for one). Fresh tenants get the shape from ``create_all`` in
``tenant_provisioning`` (the column is on the model) — this revision only
upgrades existing tenant DBs.

Idempotent + reversible: ``ADD COLUMN IF NOT EXISTS`` / ``DROP COLUMN IF EXISTS``.

**Nullable, with NO backfill — deliberately.** ``NULL`` has a defined meaning:
*"the org's reporting currency"* (``currency_conversion.resolve_reporting_currency``),
resolved at evaluation time by ``expense_policy.threshold_currency_for``. Backfilling
here is impossible-and-wrong in equal measure:

- *Impossible*: the reporting currency lives in the CONTROL-plane
  ``organizations.settings`` JSONB; this migration runs against a TENANT database
  and cannot read it.
- *Wrong*: the other candidate, ``per_diem_currency``, is server-defaulted
  ``'USD'`` on every row and was never surfaced or read, so copying it would
  freeze the exact silent-USD guess this change exists to remove.

So existing rows are neither stranded (they resolve, from live org config, to the
same unit the rest of the app already assumes for a bare number — the CFO expense
threshold, the policy table in the UI) nor silently guessed (the value is not
written; it is derived, and an admin can set it explicitly at any time).
"""

from sqlalchemy import text

from alembic import op

revision = "0077_expense_policy_currency"
down_revision = "0076_expense_currency_conversion"
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


def upgrade() -> None:
    if _has_table("expense_policies"):
        op.execute(
            "ALTER TABLE expense_policies ADD COLUMN IF NOT EXISTS threshold_currency varchar(3)"
        )


def downgrade() -> None:
    if _has_table("expense_policies"):
        op.execute("ALTER TABLE expense_policies DROP COLUMN IF EXISTS threshold_currency")
