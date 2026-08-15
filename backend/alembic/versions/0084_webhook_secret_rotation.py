"""Overlap window for outbound-webhook signing-secret rotation (control).

Adds nullable ``previous_signing_secret`` / ``previous_secret_expires_at`` to
``webhook_subscriptions``.

A subscription's HMAC signing secret was minted once at create time and shown
once, with no way to replace it. Anyone holding it can forge a signed
``invoice.approved`` / ``payment.settled`` payload into the customer's
receiver, so it is a credential with real blast radius — and the only remedy on
a leak was ``DELETE /api/webhooks/{id}`` + re-create, which changes the
subscription id and CASCADE-deletes the entire delivery history.
``docs/secrets-rotation.md`` documented self-serve rotation for the per-tenant
SCIM bearer and the OIDC client secret and said nothing about this one.

Why an overlap rather than a hard swap: with one signature header you cannot
satisfy an old-configured and a new-configured receiver at the same instant. A
bounded window during which the PREVIOUS secret also signs (delivered in a
second ``X-Webhook-Signature-Previous`` header) lets a receiver that accepts
either header rotate with zero dropped deliveries. A receiver that only reads
the primary header is no worse off than a hard swap — it simply pastes the new
secret, and its downtime is bounded by how fast it does so.

NULL on both columns is the ordinary state: no rotation in flight. The pair is
only meaningful together, and the dispatcher treats an expired
``previous_secret_expires_at`` exactly like a NULL previous secret, so a stale
window can never keep a retired secret alive.

Revision ID: 0084_webhook_secret_rotation
Revises: 0083_payment_settled_amount
Create Date: 2026-08-15

CONTROL DB ONLY: ``webhook_subscriptions`` is keyed off ``organizations`` and
is in ``tenant_provisioning.CONTROL_TABLES`` — it is never fanned to tenant
DBs. The upgrade is therefore gated on the CONTROL database (the one carrying
``organizations``), the inverse of a tenant-scoped revision like 0083, and
no-ops everywhere else. Fresh installs get the columns from the model via
``create_all``.

Idempotent: ``ADD COLUMN IF NOT EXISTS`` / ``DROP COLUMN IF EXISTS``. No
backfill — an un-rotated subscription genuinely has no previous secret, and
seeding one would put a second live signing key on every subscription.
"""

from sqlalchemy import text

from alembic import op

revision = "0084_webhook_secret_rotation"
down_revision = "0083_payment_settled_amount"
branch_labels = None
depends_on = None


def _is_control_db() -> bool:
    """The control DB is the one with the ``organizations`` table; tenant DBs
    do not have it (mirrors 0055_api_keys / 0058_api_key_usage / 0062)."""
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
    # Same width as `signing_secret` — it holds a retired value of that column.
    op.execute(
        "ALTER TABLE webhook_subscriptions "
        "ADD COLUMN IF NOT EXISTS previous_signing_secret VARCHAR(128)"
    )
    op.execute(
        "ALTER TABLE webhook_subscriptions "
        "ADD COLUMN IF NOT EXISTS previous_secret_expires_at TIMESTAMPTZ"
    )


def downgrade() -> None:
    if not _is_control_db():
        return
    op.execute(
        "ALTER TABLE webhook_subscriptions DROP COLUMN IF EXISTS previous_secret_expires_at"
    )
    op.execute("ALTER TABLE webhook_subscriptions DROP COLUMN IF EXISTS previous_signing_secret")
