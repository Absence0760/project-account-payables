"""WebAuthn / passkey credentials: webauthn_credentials table (control plane).

Adds the control-plane ``webauthn_credentials`` table backing passkey MFA — an
ADDITIONAL second factor alongside the TOTP secret on ``users.mfa_secret``. A
credential is bound to a control-plane ``User`` (``user_id`` FK), exactly where
``User.mfa_secret`` lives; it must NOT fan out to per-tenant DBs (registered in
``tenant_provisioning.CONTROL_TABLES``).

Revision ID: 0062_webauthn_credentials
Revises: 0061_vendor_website
Create Date: 2026-06-20

Control-plane DB only (gated on the ``organizations`` table existing, so it
no-ops on tenant DBs — mirrors migration 0055_api_keys).

Idempotent: ``CREATE TABLE IF NOT EXISTS`` + ``CREATE INDEX IF NOT EXISTS``.
Mirrors ``app.models.webauthn_credential.WebAuthnCredential`` exactly so a
control DB built via ``create_all`` (CI / tests) matches a migrated one.
"""

from sqlalchemy import text

from alembic import op

revision = "0062_webauthn_credentials"
down_revision = "0061_vendor_website"
branch_labels = None
depends_on = None


def _is_control_db() -> bool:
    """The control DB is the one with the ``organizations`` table; tenant DBs
    do not have it (mirrors 0055_api_keys)."""
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


_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS webauthn_credentials (
        id uuid PRIMARY KEY,
        user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        credential_id varchar(512) NOT NULL,
        public_key varchar(1024) NOT NULL,
        sign_count bigint NOT NULL DEFAULT 0,
        name varchar(120) NOT NULL DEFAULT 'Passkey',
        transports varchar(120),
        last_used_at timestamptz,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_webauthn_credentials_user_id "
    "ON webauthn_credentials (user_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_webauthn_credentials_credential_id "
    "ON webauthn_credentials (credential_id)",
]


def upgrade() -> None:
    if not _is_control_db():
        return
    for stmt in _STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    if not _is_control_db():
        return
    op.execute("DROP TABLE IF EXISTS webauthn_credentials")
