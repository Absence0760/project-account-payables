"""Custom report builder — report_definitions table.

Adds the tenant-scoped ``report_definitions`` table backing the ad-hoc report
builder: a saved report *spec* (data source + group-by dimensions + aggregate
measures + whitelisted filters + sort + optional row limit), all stored as
catalog KEYS in JSONB — never a raw column / table name. See
``app/models/report_definition.py`` + ``backend/docs/report-builder.md``.

Revision ID: 0071_report_definitions
Revises: 0070_positive_pay_currency
Create Date: 2026-07-01

Tenant DB only (gated on the ``invoices`` table, so it no-ops on the control
plane and fans out to every tenant via ``scripts/migrate_all_tenants.py`` — or
``FEOH_MIGRATE_TENANT=feoh_<slug> alembic upgrade head`` for one). Fresh tenants get
the shape from ``create_all`` in ``tenant_provisioning._create_tenant_tables``
(it's on the model); this migration only builds the table for existing tenants.

Idempotent + reversible: ``CREATE TABLE IF NOT EXISTS`` / ``CREATE INDEX IF NOT
EXISTS`` / ``DROP TABLE IF EXISTS``. Mirrors ``app.models.report_definition``
exactly so a migrated tenant matches a freshly-provisioned one. The ``entities``
FK exists by migration 0029.
"""

from sqlalchemy import text

from alembic import op

revision = "0071_report_definitions"
down_revision = "0070_positive_pay_currency"
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
    CREATE TABLE IF NOT EXISTS report_definitions (
        id uuid PRIMARY KEY,
        organization_id uuid NOT NULL,
        name varchar(200) NOT NULL,
        description text,
        data_source varchar(50) NOT NULL,
        dimensions jsonb NOT NULL DEFAULT '[]'::jsonb,
        measures jsonb NOT NULL DEFAULT '[]'::jsonb,
        filters jsonb NOT NULL DEFAULT '[]'::jsonb,
        sort jsonb NOT NULL DEFAULT '[]'::jsonb,
        row_limit integer,
        created_by_user_id uuid,
        entity_id uuid REFERENCES entities(id),
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_report_definitions_organization_id "
    "ON report_definitions (organization_id)",
    "CREATE INDEX IF NOT EXISTS ix_report_definitions_entity_id ON report_definitions (entity_id)",
]


def upgrade() -> None:
    if not _is_tenant_db():
        return
    for stmt in _STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute("DROP TABLE IF EXISTS report_definitions")
