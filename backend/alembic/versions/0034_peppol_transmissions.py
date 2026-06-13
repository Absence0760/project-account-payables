"""PEPPOL transmission log: peppol_transmissions table (tenant-scoped).

Revision ID: 0034_peppol_transmissions
Revises: 0033_quality_inspections
Create Date: 2026-06-13

Tenant DB only (gated on the ``invoices`` table, so it no-ops on the control
plane and fans out to every tenant via ``scripts/migrate_all_tenants.py``).

Idempotent: ``CREATE TABLE IF NOT EXISTS`` + ``CREATE [UNIQUE] INDEX IF NOT
EXISTS``. Mirrors ``app.models.peppol_transmission.PeppolTransmission`` (incl.
the ``entity_id`` column from ``EntityMixin`` and the two PARTIAL unique
indexes) so a fresh tenant built via ``create_all`` matches a migrated one —
the partial-index predicate text here MUST match the model's
``postgresql_where`` verbatim. See ``docs/peppol.md``.
"""

from sqlalchemy import text

from alembic import op

revision = "0034_peppol_transmissions"
down_revision = "0033_quality_inspections"
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
    CREATE TABLE IF NOT EXISTS peppol_transmissions (
        id uuid PRIMARY KEY,
        invoice_id uuid NOT NULL REFERENCES invoices(id),
        direction varchar(10) NOT NULL DEFAULT 'outbound'
            CONSTRAINT ck_peppol_direction CHECK (direction IN ('outbound','inbound')),
        participant_scheme varchar(20) NOT NULL,
        participant_value varchar(100) NOT NULL,
        sender_scheme varchar(20),
        sender_value varchar(100),
        doc_type_id text NOT NULL,
        process_id text NOT NULL,
        business_message_id varchar(100) NOT NULL,
        message_id varchar(255),
        status varchar(20) NOT NULL DEFAULT 'sending'
            CONSTRAINT ck_peppol_status CHECK (status IN ('sending','sent','delivered','failed')),
        provider varchar(50) NOT NULL,
        failure_reason varchar(255),
        transmitted_at timestamptz,
        amount numeric(15, 2),
        currency varchar(3),
        raw_response jsonb,
        organization_id uuid NOT NULL,
        entity_id uuid REFERENCES entities(id),
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_peppol_transmissions_invoice_id "
    "ON peppol_transmissions (invoice_id)",
    "CREATE INDEX IF NOT EXISTS ix_peppol_transmissions_organization_id "
    "ON peppol_transmissions (organization_id)",
    "CREATE INDEX IF NOT EXISTS ix_peppol_transmissions_entity_id "
    "ON peppol_transmissions (entity_id)",
    # The idempotency guard: at most one non-failed transmission per
    # (invoice_id, direction). A failed prior send is excluded so a retry works.
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_peppol_one_live_per_invoice_direction "
    "ON peppol_transmissions (invoice_id, direction) WHERE status <> 'failed'",
    # message_id dedupe (partial so many NULLs coexist).
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_peppol_message_id "
    "ON peppol_transmissions (message_id) WHERE message_id IS NOT NULL",
]


def upgrade() -> None:
    if not _is_tenant_db():
        return
    for stmt in _STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute("DROP TABLE IF EXISTS peppol_transmissions")
