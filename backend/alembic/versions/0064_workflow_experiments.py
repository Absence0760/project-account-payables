"""A/B testing for workflow rules: workflow_experiments table (tenant-scoped).

Revision ID: 0064_workflow_experiments
Revises: 0063_webauthn_credentials
Create Date: 2026-06-20

Tenant DB only (gated on the ``invoices`` table, so it no-ops on the control
plane and fans out to every tenant via ``scripts/migrate_all_tenants.py``).

Idempotent: ``CREATE TABLE IF NOT EXISTS`` + ``CREATE INDEX IF NOT EXISTS``.
Mirrors ``app.models.workflow_experiment.WorkflowExperiment`` (incl. the
``entity_id`` column from ``EntityMixin``) so a fresh tenant built via
``create_all`` matches a migrated one. See ``docs/adaptive-workflows.md``
§ A/B testing.
"""

from sqlalchemy import text

from alembic import op

revision = "0064_workflow_experiments"
down_revision = "0063_webauthn_credentials"
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
    CREATE TABLE IF NOT EXISTS workflow_experiments (
        id uuid PRIMARY KEY,
        organization_id uuid NOT NULL,
        name varchar(255) NOT NULL,
        description text,
        workflow_definition_id uuid NOT NULL REFERENCES workflow_definitions(id),
        config_a jsonb NOT NULL,
        config_b jsonb NOT NULL,
        split_a_pct integer NOT NULL DEFAULT 50,
        primary_metric varchar(40) NOT NULL DEFAULT 'time_to_approval_days',
        min_sample_per_variant integer NOT NULL DEFAULT 10,
        status varchar(20) NOT NULL DEFAULT 'draft',
        started_at timestamptz,
        ended_at timestamptz,
        assignments jsonb NOT NULL DEFAULT '{}'::jsonb,
        entity_id uuid REFERENCES entities(id),
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_workflow_experiments_organization_id "
    "ON workflow_experiments (organization_id)",
    "CREATE INDEX IF NOT EXISTS ix_workflow_experiments_workflow_definition_id "
    "ON workflow_experiments (workflow_definition_id)",
    "CREATE INDEX IF NOT EXISTS ix_workflow_experiments_status ON workflow_experiments (status)",
    "CREATE INDEX IF NOT EXISTS ix_workflow_experiments_org_status "
    "ON workflow_experiments (organization_id, status)",
    "CREATE INDEX IF NOT EXISTS ix_workflow_experiments_entity_id "
    "ON workflow_experiments (entity_id)",
]


def upgrade() -> None:
    if not _is_tenant_db():
        return
    for stmt in _STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute("DROP TABLE IF EXISTS workflow_experiments")
