"""Pre-match payment method on expenses (tenant).

``POST /api/corporate-card-transactions/{id}/unmatch`` cleared both legs of the
circular FK but left ``expenses.payment_method`` at whatever the match stamped —
``corporate_card`` / ``virtual_card`` — so an expense reconciled against the
wrong card line read as card-funded forever. Resetting to ``out_of_pocket``
would be a *different* wrong guess: an employee can legitimately mark an expense
card-funded before the feed row for it is imported.

``expenses.payment_method_before_match``
    what the row carried immediately BEFORE a match overwrote it. Written by the
    match path, consumed and cleared by unmatch, so a NON-NULL value means
    "currently matched, and this is the value to put back".

Revision ID: 0089_expense_pm_before_match
Revises: 0088_gl_account_code_unique
Create Date: 2026-09-04

(The revision id is kept under 32 chars — the width of
``alembic_version.version_num`` in this project's DBs.)

TENANT DB ONLY: ``expenses`` is tenant-scoped (it is not in
``tenant_provisioning.CONTROL_TABLES``, so it does not exist on the
control-plane DB). The upgrade is gated on the table existing, so the revision
no-ops on the control DB and fans out to every tenant DB via
``scripts/migrate_all_tenants.py`` (or ``FEOH_MIGRATE_TENANT=feoh_<slug> alembic
upgrade head`` for one). Fresh tenants get the shape from ``create_all`` in
``tenant_provisioning`` (the column is on the model).

Idempotent + reversible: ``ADD COLUMN IF NOT EXISTS`` / ``DROP COLUMN IF EXISTS``.

**Nullable, with NO backfill — deliberately.** A row matched before this column
existed has no recorded pre-match value, and nothing in the schema can recover
it: ``payment_method`` already holds the match's own stamp. Backfilling
``out_of_pocket`` would manufacture exactly the guess this column exists to
avoid. So NULL is given a meaning instead — *unknown, therefore do not change
it* — and ``unmatch`` leaves ``payment_method`` untouched for those rows, which
is the same state they are in today (no regression) rather than a new invented
one. A subsequent match/unmatch cycle records and restores normally.
"""

from sqlalchemy import text

from alembic import op

revision = "0089_expense_pm_before_match"
down_revision = "0088_gl_account_code_unique"
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
    if _has_table("expenses"):
        op.execute(
            "ALTER TABLE expenses ADD COLUMN IF NOT EXISTS payment_method_before_match varchar(20)"
        )


def downgrade() -> None:
    if _has_table("expenses"):
        op.execute("ALTER TABLE expenses DROP COLUMN IF EXISTS payment_method_before_match")
