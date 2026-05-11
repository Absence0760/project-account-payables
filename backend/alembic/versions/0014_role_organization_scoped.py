"""Roles: per-organization custom roles.

Revision ID: 0014_role_organization_scoped
Revises: 0013_exception_assignment_sla
Create Date: 2026-05-10

Control plane only. Adds ``organization_id`` to the ``roles`` table so an
org can mint custom roles alongside the four system roles (admin,
ap_manager, ap_clerk, cfo). System roles continue to live with
``organization_id IS NULL`` and apply across every tenant.

The existing UNIQUE constraint on ``name`` becomes ``(name,
organization_id)`` so two orgs can both have a custom "Approver" role
without colliding. Postgres treats NULL as distinct in unique
constraints by default, so multiple system roles named "admin" would in
principle slip past — there are only four system rows and they're seeded
exactly once, so we let the seed script's idempotent upsert handle that
half of the invariant.
"""

from sqlalchemy import text

from alembic import op

revision = "0014_role_organization_scoped"
down_revision = "0013_exception_assignment_sla"
branch_labels = None
depends_on = None


def _is_control_db() -> bool:
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


def upgrade() -> None:
    if not _is_control_db():
        return
    op.execute("ALTER TABLE roles ADD COLUMN IF NOT EXISTS organization_id UUID")
    op.execute("CREATE INDEX IF NOT EXISTS ix_roles_organization_id ON roles (organization_id)")
    # Drop the legacy single-column unique on name (the constraint name
    # comes from SQLAlchemy's default; we look it up via the catalog so
    # the migration is robust to differently-named seeds).
    op.execute(
        """
        DO $$
        DECLARE
            cname text;
        BEGIN
            SELECT conname INTO cname
            FROM pg_constraint
            WHERE conrelid = 'roles'::regclass
              AND contype = 'u'
              AND pg_get_constraintdef(oid) = 'UNIQUE (name)';
            IF cname IS NOT NULL THEN
                EXECUTE format('ALTER TABLE roles DROP CONSTRAINT %I', cname);
            END IF;
        END $$;
        """
    )
    op.execute("ALTER TABLE roles ADD CONSTRAINT uq_roles_name_org UNIQUE (name, organization_id)")


def downgrade() -> None:
    if not _is_control_db():
        return
    op.execute("ALTER TABLE roles DROP CONSTRAINT IF EXISTS uq_roles_name_org")
    op.execute("DROP INDEX IF EXISTS ix_roles_organization_id")
    op.execute("ALTER TABLE roles DROP COLUMN IF EXISTS organization_id")
    # Best-effort restore: the prior schema had UNIQUE (name); if rows
    # exist with duplicate names this will fail and the operator will
    # need to dedupe by hand. That's fine — a downgrade of this magnitude
    # is a manual operation anyway.
    op.execute("ALTER TABLE roles ADD CONSTRAINT roles_name_key UNIQUE (name)")
