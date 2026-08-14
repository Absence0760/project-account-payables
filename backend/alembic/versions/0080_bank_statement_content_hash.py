"""Bank-statement import idempotency: content_hash + partial unique index (tenant).

Adds a nullable ``content_hash`` column to ``bank_statements`` (sha256 hex of
the uploaded file's raw bytes) plus a partial unique index on
``(organization_id, account_identifier, content_hash) WHERE content_hash IS NOT
NULL``, so re-uploading the SAME file for the same account returns the existing
statement instead of creating a second one.

Why it matters: the second import matches nothing — every payment on it was
already claimed by the first — so it reports ``matched_count = 0``, which reads
as "this statement didn't reconcile" rather than "you imported this twice". Same
pattern as ``PositivePayFile.content_hash`` + ``uq_positive_pay_run_format``.

The index is PARTIAL so pre-existing rows (NULL hash, imported before this
revision) neither collide with each other nor need a backfill: their raw bytes
were never stored, so a hash can't be reconstructed for them.

Revision ID: 0080_bank_statement_content_hash
Revises: 0079_payment_run_plan_id
Create Date: 2026-08-14

TENANT DB ONLY: ``bank_statements`` is tenant-scoped. The upgrade is gated on
the table existing, so the revision no-ops on the control DB and fans out to
every tenant DB via ``scripts/migrate_all_tenants.py``. Fresh tenants get the
column + index from ``create_all`` in ``tenant_provisioning`` (both are declared
on the model).

Idempotent: ``ADD COLUMN IF NOT EXISTS`` / ``CREATE UNIQUE INDEX IF NOT EXISTS``
/ their ``DROP`` counterparts.
"""

from sqlalchemy import text

from alembic import op

revision = "0080_bank_statement_content_hash"
down_revision = "0079_payment_run_plan_id"
branch_labels = None
depends_on = None


def _is_tenant_db() -> bool:
    bind = op.get_bind()
    return (
        bind.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'bank_statements'"
            )
        ).scalar()
        is not None
    )


def upgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute("ALTER TABLE bank_statements ADD COLUMN IF NOT EXISTS content_hash varchar(64)")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_bank_statements_org_account_hash "
        "ON bank_statements (organization_id, account_identifier, content_hash) "
        "WHERE content_hash IS NOT NULL"
    )


def downgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute("DROP INDEX IF EXISTS uq_bank_statements_org_account_hash")
    op.execute("ALTER TABLE bank_statements DROP COLUMN IF EXISTS content_hash")
