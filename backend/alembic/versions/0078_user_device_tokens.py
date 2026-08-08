"""Mobile push device tokens: users.device_tokens (control plane).

Adds a control-plane ``device_tokens`` JSONB column to ``users`` — one FCM
token per platform (``{"ios": {"token", "updated_at"}, "android": {...}}``),
written by ``POST /api/notifications/device-token``. This is REGISTRATION
only: there is no push-SENDING adapter yet (no Firebase Admin SDK integration
exists in this codebase), so the column just gives a future dispatch feature
something to read. Device tokens are per-employee-user, not tenant business
data, so — like ``notification_prefs`` on the same model — this lives on the
control-plane ``users`` table, never fanned out to tenant DBs.

Revision ID: 0078_user_device_tokens
Revises: 0077_expense_policy_currency
Create Date: 2026-08-07

Control-plane DB only (gated on the ``organizations`` table existing, so it
no-ops on tenant DBs — mirrors migration 0063_webauthn_credentials).

Idempotent + reversible: ``ADD COLUMN IF NOT EXISTS`` / ``DROP COLUMN IF
EXISTS``. Not-null with a ``{}`` server default so existing rows backfill
cleanly with no application-level migration.
"""

from sqlalchemy import text

from alembic import op

revision = "0078_user_device_tokens"
down_revision = "0077_expense_policy_currency"
branch_labels = None
depends_on = None


def _is_control_db() -> bool:
    """The control DB is the one with the ``organizations`` table; tenant DBs
    do not have it (mirrors 0063_webauthn_credentials)."""
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
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS device_tokens jsonb "
        "NOT NULL DEFAULT '{}'::jsonb"
    )


def downgrade() -> None:
    if not _is_control_db():
        return
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS device_tokens")
