"""One payment, one bank transaction: partial unique index (tenant).

Adds a partial unique index on ``bank_transactions(matched_payment_id) WHERE
matched_payment_id IS NOT NULL``, so a ``Payment`` can be claimed by at most one
bank line. Two lines both claiming one payment double-count it as reconciled:
the payment reads as cleared twice while whichever transaction actually belongs
to a different (unrecorded, or not-yet-imported) payment silently disappears
from the unmatched view a human works from.

The invariant was only ever enforced in application code — the matcher's
``claimed`` set and ``/resolve``'s row-locked check — and neither survives two
concurrent ``POST /upload``s: each reads its ``claimed`` set before the other
commits, so both pass and both write. Same shape as
``uq_payments_one_live_per_invoice`` (migration 0074); partial because an
UNMATCHED transaction (NULL) is the normal state and many rows share it.

**Pre-existing duplicates are resolved first**, or the index could not be
created at all. For each over-claimed payment the migration keeps the EARLIEST
claimant (``created_at``, ties broken by ``id``) and clears the rest back to
unmatched (``matched_payment_id`` / ``match_method`` / ``match_confidence`` /
``matched_at`` all NULL) — exactly what ``POST .../resolve`` with a null body
does. That is the conservative direction: an un-matched transaction is VISIBLE
(it surfaces in the statement detail view and in ``GET /outstanding``'s
``unmatched_debits``, where a human re-points it), whereas a wrong match is
silent — it asserts a payment cleared when nothing proves it did. Keeping the
earliest is the least-surprising tie-break: it is the claim that was made when
the invariant still held.

``bank_statements.matched_count`` is then recomputed for every statement that
lost a claim, mirroring ``services.bank_reconciliation.is_reconciled`` in SQL,
so the denormalised rollup can't be left asserting a reconciliation the
transactions no longer support.

Revision ID: 0081_one_bank_tx_per_payment
Revises: 0080_bank_statement_content_hash
Create Date: 2026-08-14

TENANT DB ONLY: ``bank_transactions`` is tenant-scoped. The upgrade is gated on
the table existing, so the revision no-ops on the control DB and fans out to
every tenant DB via ``scripts/migrate_all_tenants.py``. Fresh tenants get the
index from ``create_all`` in ``tenant_provisioning`` (it's declared on the
model).

Idempotent: the duplicate cleanup is a no-op once no duplicates remain, and the
index uses ``CREATE UNIQUE INDEX IF NOT EXISTS`` / ``DROP INDEX IF EXISTS``.
"""

from sqlalchemy import text

from alembic import op

revision = "0081_one_bank_tx_per_payment"
down_revision = "0080_bank_statement_content_hash"
branch_labels = None
depends_on = None

# Kept in lockstep with ``services.bank_reconciliation.UNRECONCILED_MATCH_METHODS``.
# A discrepancy line is linked but NOT reconciled, so it must not count toward
# the rollup this migration recomputes.
_UNRECONCILED = "('amount_mismatch', 'currency_mismatch', 'status_conflict')"


def _is_tenant_db() -> bool:
    bind = op.get_bind()
    return (
        bind.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'bank_transactions'"
            )
        ).scalar()
        is not None
    )


def upgrade() -> None:
    if not _is_tenant_db():
        return

    # 1. Find every claimant beyond the first for a given payment. A temp table
    #    rather than one data-modifying CTE: statements inside a single CTE all
    #    see the SAME snapshot, so a recount chained onto the UPDATE would not
    #    observe the rows it just cleared.
    op.execute(
        """
        CREATE TEMP TABLE _bankrec_over_claimed ON COMMIT DROP AS
        SELECT id, statement_id
        FROM (
            SELECT
                id,
                statement_id,
                row_number() OVER (
                    PARTITION BY matched_payment_id
                    ORDER BY created_at, id
                ) AS rn
            FROM bank_transactions
            WHERE matched_payment_id IS NOT NULL
        ) ranked
        WHERE rn > 1
        """
    )

    # 2. Clear them back to unmatched — visible for a human to re-point, rather
    #    than silently asserting a clearing nothing supports.
    op.execute(
        """
        UPDATE bank_transactions bt
        SET matched_payment_id = NULL,
            match_method = NULL,
            match_confidence = NULL,
            matched_at = NULL
        FROM _bankrec_over_claimed d
        WHERE bt.id = d.id
        """
    )

    # 3. Re-derive the affected statements' reconciled rollup.
    op.execute(
        f"""
        UPDATE bank_statements s
        SET matched_count = (
            SELECT count(*)
            FROM bank_transactions t
            WHERE t.statement_id = s.id
              AND t.matched_payment_id IS NOT NULL
              AND (t.match_method IS NULL OR t.match_method NOT IN {_UNRECONCILED})
        )
        WHERE s.id IN (SELECT DISTINCT statement_id FROM _bankrec_over_claimed)
        """
    )

    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_bank_transactions_matched_payment "
        "ON bank_transactions (matched_payment_id) "
        "WHERE matched_payment_id IS NOT NULL"
    )


def downgrade() -> None:
    if not _is_tenant_db():
        return
    # Only the index is reversible — the cleared duplicate claims are not
    # restorable (and re-creating them would recreate the double-count).
    op.execute("DROP INDEX IF EXISTS uq_bank_transactions_matched_payment")
