"""Positive Pay / payment-fraud files.

Adds the ``positive_pay_files`` table (tenant-scoped). One row per generated
Positive Pay export — a ``check_issue`` file for a payment run, or a standalone
``ach_authorization`` file. The row carries only PII-free metadata; the rendered
file (which legitimately holds account numbers) lives in MinIO under
``file_key``.

Revision ID: 0048_positive_pay
Revises: 0047_vendor_statement_recon
Create Date: 2026-06-19

Tenant DB only (gated on the ``invoices`` table, so it no-ops on the control
plane and fans out to every tenant via ``scripts/migrate_all_tenants.py``).

Idempotent: ``CREATE TABLE IF NOT EXISTS`` + ``CREATE INDEX IF NOT EXISTS``.
Mirrors ``app.models.positive_pay`` exactly so a fresh tenant built via
``tenant_provisioning._create_tenant_tables`` (``create_all``) matches a
migrated one. The FKs (payment_runs, entities) exist by earlier migrations.

The partial unique index ``uq_positive_pay_run_format`` enforces one
check-issue file per ``(payment_run_id, bank_format)`` — only where
``payment_run_id IS NOT NULL`` (ACH-authorization files are run-less and never
collide).
"""

from sqlalchemy import text

from alembic import op

revision = "0048_positive_pay"
down_revision = "0047_vendor_statement_recon"
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


_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS positive_pay_files (
        id uuid PRIMARY KEY,
        organization_id uuid NOT NULL,
        payment_run_id uuid REFERENCES payment_runs(id),
        file_type varchar(20) NOT NULL,
        bank_format varchar(30) NOT NULL,
        status varchar(20) NOT NULL DEFAULT 'generated',
        item_count integer NOT NULL DEFAULT 0,
        total_amount numeric(18, 2) NOT NULL DEFAULT 0,
        content_hash varchar(64) NOT NULL,
        file_key varchar(512),
        account_last4 varchar(4),
        generated_by uuid,
        meta jsonb,
        entity_id uuid REFERENCES entities(id),
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_positive_pay_files_organization_id "
    "ON positive_pay_files (organization_id)",
    "CREATE INDEX IF NOT EXISTS ix_positive_pay_files_payment_run_id "
    "ON positive_pay_files (payment_run_id)",
    "CREATE INDEX IF NOT EXISTS ix_positive_pay_files_file_type ON positive_pay_files (file_type)",
    "CREATE INDEX IF NOT EXISTS ix_positive_pay_files_status ON positive_pay_files (status)",
    "CREATE INDEX IF NOT EXISTS ix_positive_pay_files_entity_id ON positive_pay_files (entity_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_positive_pay_run_format "
    "ON positive_pay_files (payment_run_id, bank_format) "
    "WHERE payment_run_id IS NOT NULL",
]


def upgrade() -> None:
    if not _is_tenant_db():
        return
    for stmt in _STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute("DROP TABLE IF EXISTS positive_pay_files")
