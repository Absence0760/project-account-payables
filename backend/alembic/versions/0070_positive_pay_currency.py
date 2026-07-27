"""PositivePayFile per-file currency column (tenant).

Adds ``currency`` (nullable ``VARCHAR(3)``) to ``positive_pay_files`` so a
generated Positive Pay file records the currency its ``total_amount`` is
denominated in — the org's reporting (home) currency at generation time. The
total already sums home-currency ``Payment.amount`` values, so this is a stored
LABEL, never an FX conversion; making it explicit stops the UI from having to
guess the currency of a file's total (previously it fell back to the current org
default, which is wrong once the org changes its reporting currency).

Revision ID: 0070_positive_pay_currency
Revises: 0069_card_rebate_unique
Create Date: 2026-07-01

TENANT DB ONLY: ``positive_pay_files`` is tenant-scoped (it does not exist on
the control-plane DB). The upgrade is gated on the table existing, so the
revision no-ops on the control DB and fans out to every tenant DB via
``scripts/migrate_all_tenants.py`` (or ``FEOH_MIGRATE_TENANT=ap_<slug> alembic
upgrade head`` for one). Fresh tenants get the shape from ``create_all`` in
``tenant_provisioning`` (it's on the model) — this migration only backfills
existing tenant DBs.

Idempotent + reversible: ``ADD COLUMN IF NOT EXISTS`` / ``DROP COLUMN IF
EXISTS``. Nullable with no backfill — existing rows read as "unknown currency"
and the UI falls back to the org's current reporting currency for them.
"""

from sqlalchemy import text

from alembic import op

revision = "0070_positive_pay_currency"
down_revision = "0069_card_rebate_unique"
branch_labels = None
depends_on = None


def _is_tenant_db() -> bool:
    bind = op.get_bind()
    return (
        bind.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'positive_pay_files'"
            )
        ).scalar()
        is not None
    )


def upgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute("ALTER TABLE positive_pay_files ADD COLUMN IF NOT EXISTS currency varchar(3)")


def downgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute("ALTER TABLE positive_pay_files DROP COLUMN IF EXISTS currency")
