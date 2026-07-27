"""SOX audit immutability: DB-level append-only triggers on audit_log.

Revision ID: 0022_sox_audit_immutable
Revises: 0021_scim_bearer_hash
Create Date: 2026-06-11

Tenant DB only. Installs a pair of ``BEFORE`` triggers on ``audit_log`` that
reject every DELETE and every UPDATE touching a column other than
``shipped_at`` (the carve-out the centralized shipper needs to stamp rows). The
``audit_log`` table lives only on tenant DBs, so the body is gated by
``_has_table("audit_log")`` — running against the control plane is a no-op.

The DDL itself lives in ``app/services/audit_immutability.py`` so the same
trigger install is reused by ``tenant_provisioning._create_tenant_tables`` for
tenants created via ``create_all`` (fresh tenants + the test harness). This
revision is the production fan-out across existing tenants
(``scripts/migrate_all_tenants.py`` runs it per ``FEOH_MIGRATE_TENANT``).

All statements are idempotent (``CREATE OR REPLACE`` / ``DROP ... IF EXISTS``).
"""

from sqlalchemy import text

from alembic import op
from app.services.audit_immutability import install_statements, uninstall_statements

revision = "0022_sox_audit_immutable"
down_revision = "0021_scim_bearer_hash"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    result = bind.execute(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = :t"
        ),
        {"t": table_name},
    ).scalar()
    return result is not None


def upgrade() -> None:
    if not _has_table("audit_log"):
        return
    for stmt in install_statements():
        op.execute(stmt)


def downgrade() -> None:
    if not _has_table("audit_log"):
        return
    for stmt in uninstall_statements():
        op.execute(stmt)
