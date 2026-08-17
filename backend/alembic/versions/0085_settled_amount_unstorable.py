"""A settled figure the rail reported but the column cannot hold (tenant).

Adds ``payments.settled_amount_unstorable`` (BOOLEAN NOT NULL DEFAULT false).

``settled_amount`` is ``NUMERIC(15, 2)`` — 13 integer digits. A processor
webhook reporting more than that parsed fine (``payment_adapters.parse_amount``
guards only against values so large that ``quantize`` itself raises), verified
fine, and then raised ``NumericValueOutOfRangeError`` at the flush. That took
the whole webhook transaction down with it, so:

  * the ``fraud_flag`` the verdict had ALREADY decided on was rolled back,
  * the payment's completion was never recorded,
  * the handler 5xx'd, and the processor retried into the identical failure.

The single most suspicious settlement a rail can report — one that is orders of
magnitude off what AP authorized — was the one the system recorded nothing
about, forever.

**Why a flag and not a wider column.** Widening moves the cliff without changing
the semantics; no legitimate settlement is 14 integer digits, so a value that
does not fit is not a big payment, it is a corrupt or hostile report. The right
answer is to record that we were told something we cannot represent.

**Why not simply leave ``settled_amount`` NULL.** NULL already means "no rail
ever reported a figure", which ``settlement_coverage`` deliberately fails OPEN
on so an amount-free rail (Dwolla's bare envelope) does not strand every invoice
it settles. Collapsing "reported garbage" into that would launder a figure we
know is wrong into "nothing contradicts this invoice being settled" — and mark
it paid. The two cases need to stay distinguishable, which is what this column
is for: with it set, coverage returns ``uncertain`` and the invoice holds at
``payment_scheduled`` behind the existing accept / void exits.

The reported figure itself is not lost: ``SettlementVerification.as_details``
writes it as an exact decimal string into the append-only ``audit_log`` row,
whose ``details`` is JSONB and has no such range limit. The column carries the
DECISION input; the audit row carries the evidence.

Revision ID: 0085_settled_amount_unstorable
Revises: 0084_webhook_secret_rotation

(The id omits the ``payment_`` prefix its subject would suggest: Alembic's
``alembic_version.version_num`` is ``VARCHAR(32)``, and the descriptive form ran
to 38 characters — the DDL applies, then the version bump fails, leaving the
column added but the revision unrecorded.)
Create Date: 2026-08-17

TENANT DB ONLY: ``payments`` is tenant-scoped. The upgrade is gated on the
table existing, so the revision no-ops on the control DB and fans out to every
tenant DB via ``scripts/migrate_all_tenants.py``. Fresh tenants get the column
from ``create_all`` in ``tenant_provisioning`` (declared on the model).

Idempotent: ``ADD COLUMN IF NOT EXISTS`` / ``DROP COLUMN IF EXISTS``. Existing
rows take the ``false`` default, which is correct: whatever they hold in
``settled_amount`` was storable by construction — it is already stored.
"""

from sqlalchemy import text

from alembic import op

revision = "0085_settled_amount_unstorable"
down_revision = "0084_webhook_secret_rotation"
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
    # NOT NULL DEFAULT false rather than nullable: there is no third state.
    # Either a rail reported a figure we could not store, or it did not.
    op.execute(
        "ALTER TABLE payments ADD COLUMN IF NOT EXISTS "
        "settled_amount_unstorable BOOLEAN NOT NULL DEFAULT false"
    )


def downgrade() -> None:
    if not _is_tenant_db():
        return
    op.execute("ALTER TABLE payments DROP COLUMN IF EXISTS settled_amount_unstorable")
