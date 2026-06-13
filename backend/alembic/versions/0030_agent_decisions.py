"""Exception-agent decision log: agent_decisions table (tenant-scoped).

Revision ID: 0030_agent_decisions
Revises: 0029_entities
Create Date: 2026-06-13

Tenant DB only (gated on the ``invoices`` table, so it no-ops on the control
plane and fans out to every tenant via ``scripts/migrate_all_tenants.py``).

Idempotent: ``CREATE TABLE IF NOT EXISTS`` + ``CREATE INDEX IF NOT EXISTS``.
Mirrors ``app.models.agent_decision.AgentDecision`` (incl. the ``entity_id``
column from ``EntityMixin``) so a fresh tenant built via ``create_all`` matches
a migrated one. See ``docs/exception-agents.md``.
"""

from sqlalchemy import text

from alembic import op

revision = "0030_agent_decisions"
down_revision = "0029_entities"
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
    CREATE TABLE IF NOT EXISTS agent_decisions (
        id uuid PRIMARY KEY,
        exception_id uuid NOT NULL REFERENCES exceptions(id),
        invoice_id uuid NOT NULL REFERENCES invoices(id),
        exception_type varchar(50) NOT NULL,
        action_taken varchar(20) NOT NULL,
        confidence numeric(5, 4) NOT NULL DEFAULT 0,
        rationale text,
        changes jsonb,
        autonomy_level varchar(20) NOT NULL DEFAULT 'conservative',
        agent_type varchar(50) NOT NULL,
        organization_id uuid NOT NULL,
        entity_id uuid REFERENCES entities(id),
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_agent_decisions_exception_id ON agent_decisions (exception_id)",
    "CREATE INDEX IF NOT EXISTS ix_agent_decisions_invoice_id ON agent_decisions (invoice_id)",
    "CREATE INDEX IF NOT EXISTS ix_agent_decisions_organization_id "
    "ON agent_decisions (organization_id)",
    "CREATE INDEX IF NOT EXISTS ix_agent_decisions_entity_id ON agent_decisions (entity_id)",
]


def upgrade() -> None:
    if not _is_tenant_db():
        return
    for stmt in _STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute("DROP TABLE IF EXISTS agent_decisions")
