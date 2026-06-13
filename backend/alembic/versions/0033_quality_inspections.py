"""Quality-inspection records: quality_inspections table (tenant-scoped).

Revision ID: 0033_quality_inspections
Revises: 0032_assistant
Create Date: 2026-06-13

Tenant DB only (gated on the ``invoices`` table, so it no-ops on the control
plane and fans out to every tenant via ``scripts/migrate_all_tenants.py``).

Idempotent: ``CREATE TABLE IF NOT EXISTS`` + ``CREATE INDEX IF NOT EXISTS``.
Mirrors ``app.models.quality_inspection.QualityInspection`` (incl. the
``entity_id`` column from ``EntityMixin``) so a fresh tenant built via
``create_all`` matches a migrated one. The 4th leg of 4-way matching — see
``docs/po-matching.md``.
"""

from sqlalchemy import text

from alembic import op

revision = "0033_quality_inspections"
down_revision = "0032_assistant"
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
    CREATE TABLE IF NOT EXISTS quality_inspections (
        id uuid PRIMARY KEY,
        inspection_number varchar(100) NOT NULL,
        po_id uuid REFERENCES purchase_orders(id),
        gr_id uuid REFERENCES goods_receipts(id),
        result varchar(20) NOT NULL DEFAULT 'pass',
        inspected_date date,
        inspector varchar(255),
        accepted_quantity numeric(12, 4),
        rejected_quantity numeric(12, 4),
        deviation_notes text,
        status varchar(30) DEFAULT 'completed',
        organization_id uuid NOT NULL,
        entity_id uuid REFERENCES entities(id),
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_quality_inspections_po_id ON quality_inspections (po_id)",
    "CREATE INDEX IF NOT EXISTS ix_quality_inspections_gr_id ON quality_inspections (gr_id)",
    "CREATE INDEX IF NOT EXISTS ix_quality_inspections_organization_id "
    "ON quality_inspections (organization_id)",
    "CREATE INDEX IF NOT EXISTS ix_quality_inspections_entity_id "
    "ON quality_inspections (entity_id)",
]


def upgrade() -> None:
    if not _is_tenant_db():
        return
    for stmt in _STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute("DROP TABLE IF EXISTS quality_inspections")
