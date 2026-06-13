"""Enforce role-name uniqueness on the control plane.

Revision ID: 0028_role_name_unique
Revises: 0027_intl_tax
Create Date: 2026-06-12

Control-plane DB only (the ``roles`` table lives in the control plane; tenant
DBs have no roles table, so this no-ops there). Migration 0014 added a plain
``UNIQUE(name, organization_id)`` (``uq_roles_name_org``), which keeps org-scoped
custom roles unique but does NOT constrain *system* roles: Postgres treats two
NULL ``organization_id`` values as distinct, so a control DB whose orgs were
wiped and re-seeded could accumulate duplicate system roles (admin x2, ...),
which then broke ``services.tenant_provisioning`` — its
``select(Role).where(name == "admin").scalar_one_or_none()`` raised
``MultipleResultsFound``.

This migration closes that gap:
  1. Defensively de-duplicates any existing duplicate roles (repoint
     ``user_roles`` to a canonical row per key, then drop the extras) so the
     partial unique index below can be created on a previously-polluted DB.
  2. Adds ``uq_roles_system_name`` — a partial unique index on ``name`` WHERE
     ``organization_id IS NULL`` (system roles).

Idempotent: the dedupe is a no-op when there are no duplicates, and the index
uses ``IF NOT EXISTS``. Mirrors the ``__table_args__`` on the ``Role`` model so
fresh control DBs created via ``create_all`` get the same constraint.
"""

from sqlalchemy import text

from alembic import op

revision = "0028_role_name_unique"
down_revision = "0027_intl_tax"
branch_labels = None
depends_on = None


def _is_control_db() -> bool:
    """The control DB is the one with the ``roles`` table; tenant DBs don't."""
    bind = op.get_bind()
    return (
        bind.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'roles'"
            )
        ).scalar()
        is not None
    )


# The dedupe key treats org_id NULL (system roles) as a single bucket per name.
_DEDUPE = """
WITH canon AS (
    SELECT name, organization_id, min(id::text)::uuid AS keep_id
    FROM roles
    GROUP BY name, organization_id
),
dupes AS (
    SELECT r.id AS dup_id, c.keep_id
    FROM roles r
    JOIN canon c
      ON r.name = c.name
     AND r.organization_id IS NOT DISTINCT FROM c.organization_id
    WHERE r.id <> c.keep_id
)
-- 1) drop user_role links that would collide once repointed to the canonical row
, _del AS (
    DELETE FROM user_roles ur
    USING dupes d
    WHERE ur.role_id = d.dup_id
      AND EXISTS (
          SELECT 1 FROM user_roles u2
          WHERE u2.user_id = ur.user_id AND u2.role_id = d.keep_id
      )
    RETURNING 1
)
-- 2) repoint the survivors onto the canonical role
, _upd AS (
    UPDATE user_roles ur SET role_id = d.keep_id
    FROM dupes d
    WHERE ur.role_id = d.dup_id
    RETURNING 1
)
-- 3) delete the now-unreferenced duplicate role rows
DELETE FROM roles r USING dupes d WHERE r.id = d.dup_id;
"""


def upgrade() -> None:
    if not _is_control_db():
        return
    op.execute(_DEDUPE)
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_roles_system_name "
        "ON roles (name) WHERE organization_id IS NULL"
    )


def downgrade() -> None:
    if not _is_control_db():
        return
    op.execute("DROP INDEX IF EXISTS uq_roles_system_name")
