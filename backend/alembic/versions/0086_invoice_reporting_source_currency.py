"""Which currency the invoice's reporting FX rate was fetched FOR (tenant).

Adds nullable ``invoices.reporting_source_currency`` (VARCHAR(3)) beside the
existing ``reporting_currency`` / ``reporting_amount`` / ``reporting_fx_rate`` /
``reporting_fx_locked_at`` quartet.

The reporting lock is a product derived from two MUTABLE inputs — ``amount`` and
``currency``, both editable on ``PATCH /api/invoices/{id}`` right up to approval.
``currency_conversion._lock_is_self_consistent`` decides whether the persisted
product still describes the row, and until now it could only infer the currency
pair from the SHAPE of the rate: a same-currency lock is exactly ``1``, a
cross-currency lock is not. That catches an amount edit (the figure stops
reconciling) and a currency edit that crosses the org's reporting currency
(``USD -> EUR`` on a USD-reporting org, or back). It cannot catch a correction
between two FOREIGN currencies — ``EUR -> GBP`` on a USD-reporting org with the
amount unchanged — because both checks still pass: the row records the rate but
not **which currency it was for**. The result is a stale figure that every
rollup, dashboard and export still labels "converted".

Recording the source currency makes the check exact instead of inferential.

NULL is meaningful: it means the row's lock predates this column. Those rows keep
the shape heuristic (and its residual foreign-to-foreign blind spot) until they
next re-materialize, at which point the column is filled. There is deliberately
NO backfill — ``currency`` is the invoice's currency *now*, and copying it into
``reporting_source_currency`` would assert that the rate was fetched for the
current pair, which is exactly the claim this column exists to stop us making
without evidence. A backfill would therefore launder the very rows the column is
meant to catch.

Revision ID: 0086_invoice_reporting_src_ccy
Revises: 0085_settled_amount_unstorable

(The id is truncated from the descriptive form: Alembic's
``alembic_version.version_num`` is ``VARCHAR(32)``, so a longer id applies the
DDL and then fails the version bump, leaving the column added but the revision
unrecorded — see 0085's note.)
Create Date: 2026-08-19

TENANT DB ONLY: ``invoices`` is tenant-scoped. The upgrade is gated on the table
existing, so the revision no-ops on the control DB and fans out to every tenant
DB via ``scripts/migrate_all_tenants.py``. Fresh tenants get the column from
``create_all`` in ``tenant_provisioning`` (declared on the model).

Idempotent: ``ADD COLUMN IF NOT EXISTS`` / ``DROP COLUMN IF EXISTS``.
"""

from sqlalchemy import text

from alembic import op

revision = "0086_invoice_reporting_src_ccy"
down_revision = "0085_settled_amount_unstorable"
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
    # VARCHAR(3) — the same type every other currency column uses (ISO 4217).
    op.execute("ALTER TABLE invoices ADD COLUMN IF NOT EXISTS reporting_source_currency VARCHAR(3)")


def downgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute("ALTER TABLE invoices DROP COLUMN IF EXISTS reporting_source_currency")
