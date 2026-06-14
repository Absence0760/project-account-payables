"""No-code workflow builder: workflow_versions table (tenant-scoped).

Revision ID: 0040_workflow_versions
Revises: 0039_expense_management
Create Date: 2026-06-13

Tenant DB only (gated on the ``invoices`` table, so it no-ops on the control
plane and fans out to every tenant via ``scripts/migrate_all_tenants.py``).

Idempotent: ``CREATE TABLE IF NOT EXISTS`` + ``CREATE INDEX IF NOT EXISTS``.
Mirrors ``app.models.workflow.WorkflowVersion`` exactly (incl. both the
``organization_id`` and ``definition_id`` indexes from ``index=True``) so a
fresh tenant built via ``tenant_provisioning._create_tenant_tables``
(``create_all``) matches a migrated one.
"""

from sqlalchemy import text

from alembic import op

revision = "0040_workflow_versions"
down_revision = "0039_expense_management"
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
    CREATE TABLE IF NOT EXISTS workflow_versions (
        id uuid PRIMARY KEY,
        organization_id uuid NOT NULL,
        definition_id uuid NOT NULL REFERENCES workflow_definitions(id),
        version_number integer NOT NULL,
        note text,
        steps_config jsonb NOT NULL,
        created_by uuid,
        created_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_workflow_versions_organization_id "
    "ON workflow_versions (organization_id)",
    "CREATE INDEX IF NOT EXISTS ix_workflow_versions_definition_id "
    "ON workflow_versions (definition_id)",
]


def upgrade() -> None:
    if not _is_tenant_db():
        return
    for stmt in _STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute("DROP TABLE IF EXISTS workflow_versions")
