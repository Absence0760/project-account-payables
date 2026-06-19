"""Per-(org, entity) single-default workflow uniqueness.

Multi-entity Phase 3 (see ``docs/multi-entity.md``) lets each subsidiary pick
its own workflow definition; ``workflow_engine.get_or_create_workflow_definition``
prefers the ``is_default`` definition per ``(organization_id, entity_id)``, with a
shared / org-wide (``entity_id IS NULL``) fallback. This migration enforces that
"one default" invariant at the database layer with a partial unique index.

Because SQL treats ``NULL != NULL``, a plain unique index on
``(organization_id, entity_id)`` would let two shared (NULL-entity) defaults
coexist. We COALESCE the entity to a fixed sentinel so the shared bucket is a
single distinct key. Mirrors the ``uq_entities_one_default`` partial index in the
``entities`` table for style, and matches the same index declared on the
``WorkflowDefinition`` model (so fresh tenants built via ``create_all`` — not
Alembic — already have it).

Revision ID: 0050_workflow_per_entity_default
Revises: 0049_exception_invoice_nullable
Create Date: 2026-06-19

Tenant DB only (gated on the ``workflow_definitions`` table, so it no-ops on the
control plane and fans out to every tenant via ``scripts/migrate_all_tenants.py``).

Idempotent: ``CREATE UNIQUE INDEX IF NOT EXISTS`` is a no-op when the index
already exists. Before creating it, any pre-existing duplicate defaults within a
``(org, entity)`` group are demoted (the earliest ``created_at`` stays default,
the rest are set ``is_default = false``) so the unique index can't fail
mid-fan-out on a tenant that somehow holds duplicates. The downgrade drops the
index ``IF EXISTS``.
"""

from sqlalchemy import text

from alembic import op

revision = "0050_workflow_per_entity_default"
down_revision = "0049_exception_invoice_nullable"
branch_labels = None
depends_on = None

_SENTINEL = "'00000000-0000-0000-0000-000000000000'::uuid"

# Demote extras: within each (organization_id, COALESCE(entity_id, sentinel))
# group of is_default rows, keep the earliest created_at (id as a stable final
# tiebreak) and clear the flag on the rest. Single statement, idempotent (a
# clean tenant updates zero rows).
_DEMOTE_EXTRAS = f"""
UPDATE workflow_definitions w
SET is_default = false
FROM (
    SELECT id,
           row_number() OVER (
               PARTITION BY organization_id, COALESCE(entity_id, {_SENTINEL})
               ORDER BY created_at ASC, id ASC
           ) AS rn
    FROM workflow_definitions
    WHERE is_default = true
) ranked
WHERE w.id = ranked.id AND ranked.rn > 1
"""

_CREATE_INDEX = (
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_workflow_definitions_one_default "
    "ON workflow_definitions "
    f"(organization_id, COALESCE(entity_id, {_SENTINEL})) "
    "WHERE is_default = true"
)


def _is_tenant_db() -> bool:
    bind = op.get_bind()
    return (
        bind.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'workflow_definitions'"
            )
        ).scalar()
        is not None
    )


def upgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute(_DEMOTE_EXTRAS)
    op.execute(_CREATE_INDEX)


def downgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute("DROP INDEX IF EXISTS uq_workflow_definitions_one_default")
