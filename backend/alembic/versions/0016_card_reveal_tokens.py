"""Card reveal: single-use tokens for vendor email links.

Revision ID: 0016_card_reveal_tokens
Revises: 0015_payment_run_cfo_approval
Create Date: 2026-05-10

Tenant DB only. Adds the ``card_reveal_tokens`` table — vendor-facing
links emitted when we issue a virtual card. Each row carries:

  - token_hash: sha256 of the URL-safe token (the plaintext lives only
    in the email).
  - card_id, organization_id: scoping.
  - expires_at: hard cutoff; the token endpoint refuses past it.
  - used_at: marks the first successful reveal. Tokens are single-use
    so a forwarded link can't keep handing out PANs.
"""

from sqlalchemy import text

from alembic import op

revision = "0016_card_reveal_tokens"
down_revision = "0015_payment_run_cfo_approval"
branch_labels = None
depends_on = None


def _is_tenant_db() -> bool:
    bind = op.get_bind()
    return (
        bind.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'virtual_cards'"
            )
        ).scalar()
        is not None
    )


def upgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS card_reveal_tokens (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            token_hash VARCHAR(64) NOT NULL UNIQUE,
            card_id UUID NOT NULL REFERENCES virtual_cards(id) ON DELETE CASCADE,
            organization_id UUID NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            used_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_card_reveal_tokens_card_id ON card_reveal_tokens (card_id)"
    )


def downgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute("DROP TABLE IF EXISTS card_reveal_tokens")
