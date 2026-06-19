"""Data-subject requests (GDPR / CCPA): data_subject_requests table.

Adds the tenant-scoped ``data_subject_requests`` table — the queryable history
of DSAR-export and erasure / anonymization requests the AP team services for a
data subject. Strictly PII-free: it stores only the resolved subject's UUID +
type, the request type/status, the requesting admin, and non-identifying
processing counts. The append-only ``audit_log`` rows (``privacy.dsar_export`` /
``privacy.erasure``) are the immutable record; this is the request index.

Revision ID: 0054_data_subject_requests
Revises: 0053_vendor_mfa
Create Date: 2026-06-19

Tenant DB only (gated on the ``invoices`` table, so it no-ops on the control
plane and fans out to every tenant via ``scripts/migrate_all_tenants.py``).

Idempotent: ``CREATE TABLE IF NOT EXISTS`` + ``CREATE INDEX IF NOT EXISTS``.
Mirrors ``app.models.data_subject_request`` exactly so a fresh tenant built via
``tenant_provisioning._create_tenant_tables`` (``create_all``) matches a migrated
one. The ``entities`` FK exists by migration 0029.
"""

from sqlalchemy import text

from alembic import op

revision = "0054_data_subject_requests"
down_revision = "0053_vendor_mfa"
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
    CREATE TABLE IF NOT EXISTS data_subject_requests (
        id uuid PRIMARY KEY,
        organization_id uuid NOT NULL,
        request_type varchar(20) NOT NULL,
        subject_type varchar(20) NOT NULL,
        subject_id uuid,
        status varchar(20) NOT NULL DEFAULT 'completed',
        requested_by uuid,
        completed_at timestamptz,
        record_counts jsonb,
        note varchar(500),
        fields_redacted integer NOT NULL DEFAULT 0,
        entity_id uuid REFERENCES entities(id),
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_data_subject_requests_organization_id "
    "ON data_subject_requests (organization_id)",
    "CREATE INDEX IF NOT EXISTS ix_data_subject_requests_request_type "
    "ON data_subject_requests (request_type)",
    "CREATE INDEX IF NOT EXISTS ix_data_subject_requests_subject_id "
    "ON data_subject_requests (subject_id)",
    "CREATE INDEX IF NOT EXISTS ix_data_subject_requests_entity_id "
    "ON data_subject_requests (entity_id)",
]


def upgrade() -> None:
    if not _is_tenant_db():
        return
    for stmt in _STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute("DROP TABLE IF EXISTS data_subject_requests")
