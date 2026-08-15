"""What the processor actually settled, on the payment row (tenant).

Adds nullable ``settled_amount`` / ``settled_currency`` to ``payments``.

``services/payment_settlement.verify_settlement`` already compares the amount a
processor says it settled against what AP authorized, and flags a divergence as
a payment-blocking ``fraud_flag``. What it could not do was *record* the figure:
``Payment`` carried one ``amount`` (the authorization) and a status that is
either terminal or not, with no representation of "settled for less than
authorized". So an under-settlement — the processor moved $250 against a $500
instruction — left the payment legitimately ``completed``, the invoice
transitioned to ``paid``, and the ERP / aging / 1099 YTD totals all read it as
settled in full while the vendor was short. The flag surfaced it; the money
state did not.

Holding the invoice WITHOUT this column was tried once and reverted (see
``docs/followups.md`` and the reverted commit on the previous branch):
``payment_erp_sync._sync_payments`` is the only code path that flips
``payment_scheduled → paid`` and nothing re-invokes it once a run's payments are
terminal, so a hold keyed on a resolvable flag stranded the invoice permanently
— never ``paid``, never re-payable. A hold is only safe once the settled figure
is ON the row (so the condition is a durable fact about the payment rather than
the transient state of an exception someone is expected to clear) and a real
release exists. Both land with this column.

NULL is meaningful and is NOT zero: it means no processor ever reported a
settled figure for this payment — an adapter whose webhook genuinely omits the
amount (Dwolla's bare envelope), or a row that predates this column. The
coverage classifier treats NULL as "nothing indicates a shortfall" and fails
OPEN, exactly as the verifier treats an absent amount as ``unverified`` rather
than as evidence. That is what keeps an amount-free rail from stranding every
invoice it settles.

Revision ID: 0083_payment_settled_amount
Revises: 0082_payment_retry_attempt_chain
Create Date: 2026-08-15

TENANT DB ONLY: ``payments`` is tenant-scoped. The upgrade is gated on the
table existing, so the revision no-ops on the control DB and fans out to every
tenant DB via ``scripts/migrate_all_tenants.py``. Fresh tenants get the columns
from ``create_all`` in ``tenant_provisioning`` (declared on the model).

Idempotent: ``ADD COLUMN IF NOT EXISTS`` / ``DROP COLUMN IF EXISTS``. No
backfill — a historical payment has no settled figure to recover, and
manufacturing one from ``amount`` would assert a reconciliation that never
happened.
"""

from sqlalchemy import text

from alembic import op

revision = "0083_payment_settled_amount"
down_revision = "0082_payment_retry_attempt_chain"
branch_labels = None
depends_on = None


def _is_tenant_db() -> bool:
    bind = op.get_bind()
    return (
        bind.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'payments'"
            )
        ).scalar()
        is not None
    )


def upgrade() -> None:
    if not _is_tenant_db():
        return
    # NUMERIC(15, 2) — the same exact type `payments.amount` uses. Money is
    # never a float (project invariant); a settled figure compared against an
    # authorized one at cent tolerance has to be exact on both sides.
    op.execute("ALTER TABLE payments ADD COLUMN IF NOT EXISTS settled_amount NUMERIC(15, 2)")
    op.execute("ALTER TABLE payments ADD COLUMN IF NOT EXISTS settled_currency VARCHAR(3)")


def downgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute("ALTER TABLE payments DROP COLUMN IF EXISTS settled_currency")
    op.execute("ALTER TABLE payments DROP COLUMN IF EXISTS settled_amount")
