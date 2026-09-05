"""Passkeys on a vanity host: webauthn_credentials.rp_id (control plane).

A WebAuthn credential is bound to exactly ONE registrable domain (its Relying
Party ID). ``FEOH_WEBAUTHN_RP_ID`` was a single global, so a tenant served on a
custom domain (``ap.acmecorp.com``) could neither register nor use a passkey.
The RP ID is now resolved per request from the host the ceremony runs on
(``app/services/webauthn_rp.py``), which means a stored credential has to record
which RP ID it was registered under — otherwise a passkey minted on one host
fails with an opaque signature error on the other instead of the legible "this
passkey belongs to <host>; register one here".

Backfill: every existing row is stamped with the configured global RP ID. That
is not a guess — a single global is provably the only RP ID any of them could
have been registered under, because per-host resolution did not exist until this
change. The column stays NULLABLE (and ``webauthn_rp.effective_rp_id`` reads a
NULL as the global) so an old worker still inserting during a rolling deploy
can't hit a NOT NULL violation on the money-adjacent auth path.

``webauthn_credentials`` is a CONTROL-PLANE table (registered in
``tenant_provisioning.CONTROL_TABLES``) — it hangs off ``users.id`` and never
lands in a tenant DB. So this migration is control-plane-ONLY and must not fan
out: gated on the ``organizations`` table existing, exactly like the migration
that created the table (0063) and 0065 / 0062 / 0055. It no-ops on a tenant DB.

Revision ID: 0091_webauthn_cred_rp_id
Revises: 0090_invoice_budget_dim_idx
Create Date: 2026-09-05

Idempotent: ``ADD COLUMN IF NOT EXISTS`` plus a ``WHERE rp_id IS NULL`` backfill
that is a no-op on re-run. Mirrors
``app.models.webauthn_credential.WebAuthnCredential`` exactly so a control DB
built via ``create_all`` (CI / tests) matches a migrated one.
"""

from sqlalchemy import text

from alembic import op

revision = "0091_webauthn_cred_rp_id"
down_revision = "0090_invoice_budget_dim_idx"
branch_labels = None
depends_on = None

# Last-resort backfill value, matching `config.Settings.webauthn_rp_id` /
# `services.webauthn_rp.DEFAULT_RP_ID`. Only used when the deployment's own
# setting is blank — a blank RP ID would make every ceremony fail, so it is
# never written into the column.
_FALLBACK_RP_ID = "localhost"

# The two statements, as constants so the regression test can execute exactly
# what the migration executes rather than a paraphrase of it.
ADD_COLUMN_SQL = "ALTER TABLE webauthn_credentials ADD COLUMN IF NOT EXISTS rp_id varchar(255)"
# Idempotent by construction: a second run matches no rows.
BACKFILL_SQL = "UPDATE webauthn_credentials SET rp_id = :rp_id WHERE rp_id IS NULL"


def _is_control_db() -> bool:
    """The control DB is the one with the ``organizations`` table; tenant DBs
    do not have it (mirrors 0063_webauthn_credentials / 0065_org_parent)."""
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


def configured_rp_id() -> str:
    """The RP ID this deployment has been running under — what every existing
    credential was necessarily registered against.

    Read from the app settings rather than hardcoded so a deployment on a real
    apex backfills to ITS apex, not to the dev default. Normalized the same way
    ``webauthn_rp.platform_rp_id`` normalizes it (bare lowercase host), so the
    backfilled value compares equal to what the resolver returns at runtime.
    """
    try:
        from app.config import settings

        raw = (settings.webauthn_rp_id or "").strip().lower()
    except Exception:  # pragma: no cover - settings should always import
        raw = ""
    return raw or _FALLBACK_RP_ID


def upgrade() -> None:
    if not _is_control_db():
        return
    op.execute(ADD_COLUMN_SQL)
    op.get_bind().execute(text(BACKFILL_SQL), {"rp_id": configured_rp_id()})


def downgrade() -> None:
    if not _is_control_db():
        return
    op.execute("ALTER TABLE webauthn_credentials DROP COLUMN IF EXISTS rp_id")
