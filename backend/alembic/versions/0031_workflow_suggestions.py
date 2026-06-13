"""Adaptive-workflow advisory suggestions: workflow_suggestions table (tenant-scoped).

Revision ID: 0031_workflow_suggestions
Revises: 0030_agent_decisions
Create Date: 2026-06-13

Tenant DB only (gated on the ``invoices`` table, so it no-ops on the control
plane and fans out to every tenant via ``scripts/migrate_all_tenants.py``).

Idempotent: ``CREATE TABLE IF NOT EXISTS`` + ``CREATE INDEX IF NOT EXISTS``.
Mirrors ``app.models.adaptive_suggestion.WorkflowSuggestion`` (incl. the
``entity_id`` column from ``EntityMixin``) so a fresh tenant built via
``create_all`` matches a migrated one. See ``docs/adaptive-workflows.md``.
"""

from sqlalchemy import text

from alembic import op

revision = "0031_workflow_suggestions"
down_revision = "0030_agent_decisions"
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
    CREATE TABLE IF NOT EXISTS workflow_suggestions (
        id uuid PRIMARY KEY,
        organization_id uuid NOT NULL,
        kind varchar(40) NOT NULL,
        dedupe_key varchar(200) NOT NULL,
        vendor_id uuid,
        vendor_name varchar(255) NOT NULL DEFAULT '',
        title text NOT NULL,
        rationale text,
        payload jsonb NOT NULL,
        confidence_pct numeric(5, 2) NOT NULL DEFAULT 0,
        status varchar(20) NOT NULL DEFAULT 'open',
        dismissed_by uuid,
        dismissed_at timestamptz,
        entity_id uuid REFERENCES entities(id),
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_workflow_suggestions_dedupe_key "
    "ON workflow_suggestions (dedupe_key)",
    "CREATE INDEX IF NOT EXISTS ix_workflow_suggestions_organization_id "
    "ON workflow_suggestions (organization_id)",
    "CREATE INDEX IF NOT EXISTS ix_workflow_suggestions_vendor_id "
    "ON workflow_suggestions (vendor_id)",
    "CREATE INDEX IF NOT EXISTS ix_workflow_suggestions_status ON workflow_suggestions (status)",
    "CREATE INDEX IF NOT EXISTS ix_workflow_suggestions_entity_id "
    "ON workflow_suggestions (entity_id)",
]


def upgrade() -> None:
    if not _is_tenant_db():
        return
    for stmt in _STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute("DROP TABLE IF EXISTS workflow_suggestions")
