"""Granular role permissions: roles.permissions JSONB column (control plane).

Adds a nullable ``permissions`` JSONB column to the control-plane ``roles``
table so a CUSTOM role can carry an explicit list of granular permission strings
(see ``app/api/permissions.py``). System roles (``organization_id IS NULL``)
leave it NULL and resolve via the static default-permission map instead — so the
four built-in roles behave identically before and after this change.

``roles`` is a CONTROL-PLANE table (it lives in
``tenant_provisioning.CONTROL_TABLES`` and ``scripts/seed.py::CONTROL_TABLES``),
so this migration is control-plane-ONLY and must NOT fan out to per-tenant DBs —
gated on the ``organizations`` table existing, exactly like migration 0055 /
0058. It no-ops on a tenant DB.

Revision ID: 0062_role_permissions
Revises: 0061_vendor_website
Create Date: 2026-06-20

Idempotent: ``ADD COLUMN IF NOT EXISTS``. Mirrors ``app.models.user.Role``
exactly so a control DB built via ``create_all`` (CI / tests) matches a migrated
one.
"""

from sqlalchemy import text

from alembic import op

revision = "0062_role_permissions"
down_revision = "0061_vendor_website"
branch_labels = None
depends_on = None


def _is_control_db() -> bool:
    """The control DB is the one with the ``organizations`` table; tenant DBs
    do not have it (mirrors 0055_api_keys / 0058_api_key_usage)."""
    bind = op.get_bind()
    return (
        bind.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'organizations'"
            )
        ).scalar()
        is not None
    )


def upgrade() -> None:
    if not _is_control_db():
        return
    op.execute("ALTER TABLE roles ADD COLUMN IF NOT EXISTS permissions jsonb")


def downgrade() -> None:
    if not _is_control_db():
        return
    op.execute("ALTER TABLE roles DROP COLUMN IF EXISTS permissions")
