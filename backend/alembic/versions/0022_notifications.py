"""Email & notification system.

Revision ID: 0022_notifications
Revises: 0021_scim_bearer_hash
Create Date: 2026-06-11

Two-part migration (same DB URL is reused for both control and tenant fan-out,
so each branch probes the schema it expects and no-ops on the other):

- **Tenant DB** (has `invoices`): create the `notifications` table + the
  recipient and recipient/unread indexes that back the per-user list and the
  unread-count badge query.
- **Control DB** (has `users`): add the `notification_prefs` JSONB column to
  `users` (per-user, user-global notification channel preferences).

All DDL is `IF NOT EXISTS` / `IF EXISTS`, so it is idempotent and safe to run
against dev DBs that were originally bootstrapped via `Base.metadata.create_all`
as well as via the migration chain. Fans out to every tenant through
`scripts/migrate_all_tenants.py`.

NOTE (parallel-branch integration): four sibling features are being built on
their own branches concurrently and their migrations are not visible here. This
revision id (`0022_notifications`) and its `down_revision` may collide at
integration time and need renumbering, or a merge revision, to linearise the
Alembic history.
"""

from sqlalchemy import text

from alembic import op

revision = "0022_notifications"
down_revision = "0021_scim_bearer_hash"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
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
    # ---- Tenant branch: notifications table -------------------------------
    if _has_table("invoices"):
        op.execute(
            """
            CREATE TABLE IF NOT EXISTS notifications (
                id UUID PRIMARY KEY,
                correlation_id UUID NOT NULL,
                organization_id UUID NOT NULL,
                recipient_user_id UUID NOT NULL,
                event_type VARCHAR(50) NOT NULL,
                entity_type VARCHAR(30) NOT NULL DEFAULT 'invoice',
                entity_id UUID,
                title VARCHAR(255) NOT NULL,
                body TEXT,
                read_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_notifications_organization_id "
            "ON notifications (organization_id)"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_notifications_recipient "
            "ON notifications (recipient_user_id)"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_notifications_recipient_unread "
            "ON notifications (recipient_user_id, read_at)"
        )

    # ---- Control branch: users.notification_prefs -------------------------
    if _has_table("users"):
        op.execute(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS notification_prefs "
            "JSONB NOT NULL DEFAULT '{}'::jsonb"
        )


def downgrade() -> None:
    if _has_table("notifications"):
        op.execute("DROP INDEX IF EXISTS ix_notifications_recipient_unread")
        op.execute("DROP INDEX IF EXISTS ix_notifications_recipient")
        op.execute("DROP INDEX IF EXISTS ix_notifications_organization_id")
        op.execute("DROP TABLE IF EXISTS notifications")

    if _has_table("users"):
        op.execute("ALTER TABLE users DROP COLUMN IF EXISTS notification_prefs")
