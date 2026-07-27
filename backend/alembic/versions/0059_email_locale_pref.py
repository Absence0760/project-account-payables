"""Email language preference: users.locale (control) + vendor_users.locale (tenant).

Adds an account-level ``locale`` column to BOTH ``users`` (control plane) and
``vendor_users`` (tenant-scoped) backing the DB-synced "what language to email
this person" preference consumed by the per-locale email catalogue
(``app/services/email_adapters/email_catalogue.py``). It is deliberately SEPARATE
from any per-device UI locale — it drives outbound email only, never in-app UI.

Revision ID: 0059_email_locale_pref
Revises: 0058_api_key_usage
Create Date: 2026-06-20

DUAL-TARGET, EXISTENCE-GUARDED: this single revision runs against the control DB
(``feohledger`` — has ``users``, no ``vendor_users``) AND every tenant DB
(``feoh_<slug>`` — has ``vendor_users``, no ``users``). Each ``ADD COLUMN`` is
guarded by an ``information_schema`` table-existence check, so the column lands
ONLY where its table exists — the same revision is safe on both. The control DB
is migrated by ``alembic upgrade head``; every tenant DB by
``scripts/migrate_all_tenants.py`` (or ``FEOH_MIGRATE_TENANT=feoh_<slug> alembic
upgrade head`` for one). Fresh tenants get the column from ``create_all`` in
``tenant_provisioning`` (model field) — this migration only backfills existing
DBs.

Idempotent + reversible: ``ADD COLUMN IF NOT EXISTS`` / ``DROP COLUMN IF EXISTS``.
The column is nullable (NULL = English fallback) — no default needed.
"""

from sqlalchemy import text

from alembic import op

revision = "0059_email_locale_pref"
down_revision = "0058_api_key_usage"
branch_labels = None
depends_on = None


def _table_exists(table_name: str) -> bool:
    bind = op.get_bind()
    return (
        bind.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = :t"
            ),
            {"t": table_name},
        ).scalar()
        is not None
    )


def upgrade() -> None:
    # Control DB only: users.locale
    if _table_exists("users"):
        op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS locale varchar(16)")
    # Tenant DBs only: vendor_users.locale
    if _table_exists("vendor_users"):
        op.execute("ALTER TABLE vendor_users ADD COLUMN IF NOT EXISTS locale varchar(16)")


def downgrade() -> None:
    if _table_exists("users"):
        op.execute("ALTER TABLE users DROP COLUMN IF EXISTS locale")
    if _table_exists("vendor_users"):
        op.execute("ALTER TABLE vendor_users DROP COLUMN IF EXISTS locale")
