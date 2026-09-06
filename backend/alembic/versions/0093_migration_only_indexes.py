"""Close the migration-only-index class of drift: 18 indexes that existed in a
migration and in no model, plus the two spellings that had drifted apart.

Round 24 found ONE instance of this and fixed it — ``ix_audit_log_shipped_at_null``
(migration 0010, restated in 0092's ``_ADOPTED``). It is not one instance, it is
a class, and this revision closes the class.

The defect
----------
A tenant is provisioned two ways, and only one of them runs Alembic:

* an EXISTING tenant gets ``alembic upgrade head``
  (``scripts/migrate_all_tenants.py``);
* a NEW tenant gets ``Base.metadata.create_all``
  (``services/tenant_provisioning._create_tenant_tables``), and the control
  plane is built the same way by the test harness / a fresh install.

``create_all`` builds exactly what the ORM declares. So an index written into a
migration but never declared on its model **silently never reaches a
freshly-provisioned tenant**, and the two provisioning paths produce
permanently different schemas. Nothing in the system notices: the queries still
return correct rows, just by sequential scan — and where the index is UNIQUE,
the invariant it enforces is simply absent.

Verified, not assumed. On this machine, ``feoh_pytesta`` (built by ``create_all``,
no ``alembic_version`` row at all) and ``feohledger_pytest`` were missing all 20
indexes the audit turned up, every one of which a migration creates.

Two of the twenty are UNIQUE, i.e. correctness rather than performance
------------------------------------------------------------------------
* ``uq_positive_pay_run_format`` — ``POST /api/positive-pay/payment-runs/{id}/
  check-issue`` is a read-then-insert: it SELECTs an existing file for
  ``(run, bank_format)``, returns it if found, otherwise renders + uploads +
  inserts. The ONLY thing making that safe under concurrency is this index
  turning the loser into the ``IntegrityError`` the handler catches. Without it
  two concurrent calls (a double-clicked button; a client retry after a
  timeout) both find nothing and both insert — two Positive Pay files for one
  run, so two MinIO objects carrying full account/routing numbers, and the
  handler's own ``scalar_one_or_none()`` idempotency lookup then raises
  ``MultipleResultsFound`` on every subsequent call: a permanent 500 on that
  run, with no way back except deleting a row by hand.
* ``uq_subscription_one_live_per_org`` — control-plane. ``uq_subscription_org_plan``
  (the one the model DID declare) does not bound the live count: two rows for
  two different plans satisfy it, which is exactly the double-billing shape the
  partial unique index refuses.

What this revision does
-----------------------
1. **Ensures — does not own — 18 indexes** (``_ENSURED``). Each was created by
   an earlier revision, which still owns it; the fix is the model declaration
   this revision ships alongside, and the ``CREATE INDEX IF NOT EXISTS`` here
   only catches an ALREADY-provisioned tenant that reaches this revision.
   Consequently ``downgrade()`` does **not** drop any of them: reverting this
   revision must not remove migration 0004's / 0013's / 0017's / … index. Same
   ``_ADOPTED`` semantics 0092 established.

2. **Reconciles the two indexes that were NOT missing, only misspelled**
   (``_RECONCILE``) — the audit's two false positives, resolved in the other
   direction because declaring either on its model would create a genuine
   duplicate:

   * ``ix_bank_transactions_matched_payment`` (0019) is
     ``(matched_payment_id) WHERE matched_payment_id IS NOT NULL`` — the exact
     column and predicate of ``uq_bank_transactions_matched_payment``, the
     UNIQUE index migration 0081 added and the model declares. A unique index
     serves every read the non-unique one could, so on a migrated tenant 0019's
     index has been pure write overhead on a hot table since 0081 landed. It is
     dropped here; ``downgrade`` recreates it.
   * ``ix_vendor_change_requests_org_id`` (0022) and the model's
     ``ix_vendor_change_requests_organization_id`` (SQLAlchemy's default name
     for ``index=True`` on that column) are the same index on the same column
     under two names. This revision converges on the model's name — creating it
     where absent, dropping the migration-only alias — so the two builds agree
     exactly instead of "agreeing except for a name".

   ``downgrade`` restores everything the upgrade dropped. It deliberately does
   NOT drop ``ix_vendor_change_requests_organization_id``: the model declares
   it, so removing it would re-create the very drift this revision closes, and
   every ``create_all``-provisioned tenant would still have it anyway.

3. **Refuses rather than half-applies** where a UNIQUE index is involved. A
   tenant that has been running WITHOUT ``uq_positive_pay_run_format`` may
   already hold the duplicate rows it was supposed to prevent, and
   ``CREATE UNIQUE INDEX`` would then fail mid-revision with a bare Postgres
   error naming an index. ``_UNIQUE_PREFLIGHT`` counts the offending groups
   first and raises an actionable, PII-free message (counts only — never an
   account number, never a bearer digest) before any DDL runs. Cleaning up is a
   judgement call about real artefacts (which of two Positive Pay files went to
   the bank; which of two subscriptions is billing), so it is deliberately the
   operator's, not a silent ``DELETE`` inside a migration.

Why the model declaration is the fix and the CREATE here is only a catch-up:
new tenants keep being provisioned by ``create_all``, so a migration alone
would leave the next tenant in exactly the state this revision is repairing.
``tests/test_migration_model_index_parity.py`` is the systemic guard — every
``CREATE [UNIQUE] INDEX`` in every revision, checked against the models, opt-out
with a written reason — so the class cannot silently reopen.

Why NOT ``CREATE INDEX CONCURRENTLY``: unchanged from 0092's reasoning —
``CONCURRENTLY`` cannot run inside Alembic's transaction, and combined with
``IF NOT EXISTS`` a cancelled build leaves an INVALID index that every later run
skips while reporting success. An operator with a table large enough to care
builds these by hand with ``CONCURRENTLY``, verifies ``pg_index.indisvalid``, and
then runs this revision, where every statement becomes a no-op.

Revision ID: 0093_migration_only_indexes
Revises: 0092_list_and_audit_indexes
Create Date: 2026-09-06

BOTH DATABASES. Unlike 0092, this revision spans the split: 15 of the ensured
indexes are on tenant tables and 3 (``organizations``, ``users``,
``subscriptions`` — all in ``tenant_provisioning.CONTROL_TABLES``) are on
control-plane ones. Every statement is gated on its own table existing, so each
half no-ops where its tables are absent and the one revision serves
``alembic upgrade head`` on the control plane AND
``scripts/migrate_all_tenants.py`` across every tenant.

Idempotent + reversible: ``CREATE INDEX IF NOT EXISTS`` / ``DROP INDEX IF
EXISTS``.

See ``backend/docs/database.md`` § Index parity between the two provisioning
paths and ``backend/docs/positive-pay.md`` § Idempotency.
"""

from sqlalchemy import text

from alembic import op

revision = "0093_migration_only_indexes"
down_revision = "0092_list_and_audit_indexes"
branch_labels = None
depends_on = None


#: Indexes this revision ENSURES EXIST but does not OWN — each is created by the
#: revision named in the trailing comment, which still owns its DROP. The real
#: fix is the model declaration shipped with this revision; these restatements
#: only catch a tenant that was provisioned before it. Deliberately absent from
#: ``_DOWNGRADE``.
#:
#: ``(table, index name, index body)`` — the body is everything after ``ON``,
#: copied from the owning revision so the two spellings cannot drift.
_ENSURED: list[tuple[str, str, str]] = [
    # --- control plane ----------------------------------------------------
    # 0004: the SSO callback's identity lookup, on every OIDC/SAML sign-in.
    (
        "users",
        "ix_users_sso_lookup",
        "ON users (sso_provider, sso_provider_id) WHERE sso_provider IS NOT NULL",
    ),
    # 0021: resolves the tenant from the SCIM bearer digest on every SCIM call.
    (
        "organizations",
        "ix_organizations_scim_bearer_hash",
        "ON organizations (scim_bearer_hash) WHERE scim_bearer_hash IS NOT NULL",
        # UNIQUE — see _UNIQUE_INDEX_NAMES.
    ),
    # 0056: "at most one LIVE subscription per org" — the billing invariant.
    (
        "subscriptions",
        "uq_subscription_one_live_per_org",
        "ON subscriptions (organization_id) WHERE status <> 'canceled'",
    ),
    # --- tenant -----------------------------------------------------------
    # 0003: pgvector HNSW, the RAG similarity search's whole point. Expressible
    # on the model via postgresql_using / postgresql_ops, so no exemption is
    # needed — a plain btree here would have been the wrong declaration.
    (
        "invoice_embeddings",
        "ix_invoice_embeddings_embedding_hnsw",
        "ON invoice_embeddings USING hnsw (embedding vector_cosine_ops)",
    ),
    # 0013: the exception SLA sweep — overdue AND still live.
    (
        "exceptions",
        "ix_exceptions_due_at",
        "ON exceptions (due_at) WHERE status IN ('open', 'escalated')",
    ),
    # 0017: corridor analytics; partial because a domestic payment is NULL.
    (
        "payments",
        "ix_payments_corridor",
        "ON payments (corridor) WHERE corridor IS NOT NULL",
    ),
    # 0018: "most recent screening for this vendor", and the review queue.
    (
        "sanctions_checks",
        "ix_sanctions_checks_vendor_id",
        "ON sanctions_checks (vendor_id, checked_at DESC)",
    ),
    (
        "sanctions_checks",
        "ix_sanctions_checks_result",
        "ON sanctions_checks (result) WHERE result IN ('match', 'review_required')",
    ),
    # 0019: one statement's lines in date order; the org-wide worksheet.
    (
        "bank_transactions",
        "ix_bank_transactions_statement",
        "ON bank_transactions (statement_id, transaction_date)",
    ),
    (
        "bank_transactions",
        "ix_bank_transactions_org_date",
        "ON bank_transactions (organization_id, transaction_date DESC)",
    ),
    # 0020: `list_due_schedules`, per tenant, every tick, forever.
    (
        "scheduled_reports",
        "ix_scheduled_reports_due",
        "ON scheduled_reports (next_run_at) WHERE enabled = true",
    ),
    # 0022: the AP approval queue reads only `pending` rows.
    (
        "vendor_change_requests",
        "ix_vendor_change_requests_pending",
        "ON vendor_change_requests (status) WHERE status = 'pending'",
    ),
    # 0033: the 4-way match's inspection lookup — receipt first, then PO.
    (
        "quality_inspections",
        "ix_quality_inspections_po_id",
        "ON quality_inspections (po_id)",
    ),
    (
        "quality_inspections",
        "ix_quality_inspections_gr_id",
        "ON quality_inspections (gr_id)",
    ),
    # 0042: screening review queue, re-screen sweep, blocked-vendor surface.
    (
        "vendors",
        "ix_vendors_screening_status",
        "ON vendors (screening_status)",
    ),
    (
        "vendors",
        "ix_vendors_last_screened_at",
        "ON vendors (last_screened_at)",
    ),
    (
        "vendors",
        "ix_vendors_payments_blocked",
        "ON vendors (payments_blocked) WHERE payments_blocked",
    ),
    # 0048: THE idempotency backstop for the check-issue endpoint.
    (
        "positive_pay_files",
        "uq_positive_pay_run_format",
        "ON positive_pay_files (payment_run_id, bank_format) WHERE payment_run_id IS NOT NULL",
    ),
]

#: The ensured indexes that are UNIQUE. Kept as a set rather than a flag on each
#: tuple so ``_ENSURED`` stays a verbatim copy of the owning revisions' bodies.
_UNIQUE_INDEX_NAMES = frozenset(
    {
        "ix_organizations_scim_bearer_hash",
        "uq_subscription_one_live_per_org",
        "uq_positive_pay_run_format",
    }
)

#: Pre-flight for the three UNIQUE indexes: a tenant that ran without one may
#: already hold the rows it was meant to prevent, and ``CREATE UNIQUE INDEX``
#: would fail mid-revision with a bare Postgres error. Count the offending
#: groups first and refuse with something actionable. Counts only — no account
#: number, no bearer digest, no org name ever enters the message.
#:
#: ``(table, duplicate-group-counting SQL, what the operator has to decide)``
_UNIQUE_PREFLIGHT: list[tuple[str, str, str]] = [
    (
        "positive_pay_files",
        "SELECT count(*) FROM (SELECT payment_run_id, bank_format "
        "FROM positive_pay_files WHERE payment_run_id IS NOT NULL "
        "GROUP BY payment_run_id, bank_format HAVING count(*) > 1) d",
        "duplicate Positive Pay check-issue file(s) for the same "
        "(payment_run, bank_format). Decide which file was actually sent to the "
        "bank and delete the others via DELETE /api/positive-pay/{id} (which "
        "also removes the stored object), then re-run this migration",
    ),
    (
        "subscriptions",
        "SELECT count(*) FROM (SELECT organization_id FROM subscriptions "
        "WHERE status <> 'canceled' GROUP BY organization_id HAVING count(*) > 1) d",
        "organization(s) with more than one live subscription. Cancel the "
        "superseded row(s) (status = 'canceled') so exactly one remains live "
        "per org, then re-run this migration",
    ),
    (
        "organizations",
        "SELECT count(*) FROM (SELECT scim_bearer_hash FROM organizations "
        "WHERE scim_bearer_hash IS NOT NULL "
        "GROUP BY scim_bearer_hash HAVING count(*) > 1) d",
        "organization(s) sharing a SCIM bearer digest. Re-mint the token for all "
        "but one via the /api/organization SCIM token endpoint, then re-run this "
        "migration",
    ),
]

#: The two indexes the audit flagged that were NOT actually missing — the same
#: index under another name, or superseded by a stronger one. Declaring either
#: on its model would build a genuine duplicate, so they are reconciled here
#: instead. See the module docstring.
#:
#: ``(table, upgrade statement, downgrade statement)``
_RECONCILE: list[tuple[str, str, str]] = [
    # Superseded by `uq_bank_transactions_matched_payment` (0081, model-declared)
    # — identical column, identical predicate, and UNIQUE. Pure write overhead
    # on a migrated tenant; absent on a fresh one.
    (
        "bank_transactions",
        "DROP INDEX IF EXISTS ix_bank_transactions_matched_payment",
        "CREATE INDEX IF NOT EXISTS ix_bank_transactions_matched_payment "
        "ON bank_transactions (matched_payment_id) WHERE matched_payment_id IS NOT NULL",
    ),
    # Converge on the model's name. Create-then-drop, not ALTER INDEX RENAME:
    # a rename is only correct on a tenant that has the source and not the
    # target, and this has to be a no-op on both of the shapes that exist.
    (
        "vendor_change_requests",
        "CREATE INDEX IF NOT EXISTS ix_vendor_change_requests_organization_id "
        "ON vendor_change_requests (organization_id)",
        # Restores 0022's name. Deliberately does not drop the model's — see the
        # module docstring.
        "CREATE INDEX IF NOT EXISTS ix_vendor_change_requests_org_id "
        "ON vendor_change_requests (organization_id)",
    ),
    (
        "vendor_change_requests",
        "DROP INDEX IF EXISTS ix_vendor_change_requests_org_id",
        "",  # paired with the CREATE above; nothing to undo separately
    ),
]


def _create(name: str, body: str) -> str:
    unique = "UNIQUE " if name in _UNIQUE_INDEX_NAMES else ""
    return f"CREATE {unique}INDEX IF NOT EXISTS {name} {body}"


_UPGRADE: list[tuple[str, str]] = [
    (table, _create(name, body)) for table, name, body in _ENSURED
] + [(table, statement) for table, statement, _down in _RECONCILE]

_DOWNGRADE: list[tuple[str, str]] = [
    (table, statement) for table, _up, statement in reversed(_RECONCILE) if statement
]


def _table_exists(name: str) -> bool:
    return (
        op.get_bind()
        .execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = :name"
            ),
            {"name": name},
        )
        .scalar()
        is not None
    )


def _run(statements: list[tuple[str, str]]) -> None:
    seen: dict[str, bool] = {}
    for table, stmt in statements:
        if table not in seen:
            seen[table] = _table_exists(table)
        if seen[table]:
            op.execute(stmt)


def _preflight() -> None:
    """Refuse before any DDL if a UNIQUE index cannot be built."""
    bind = op.get_bind()
    for table, sql, remedy in _UNIQUE_PREFLIGHT:
        if not _table_exists(table):
            continue
        offending = bind.execute(text(sql)).scalar() or 0
        if offending:
            raise RuntimeError(f"Cannot enforce uniqueness on {table}: found {offending} {remedy}.")


def upgrade() -> None:
    _preflight()
    _run(_UPGRADE)


def downgrade() -> None:
    _run(_DOWNGRADE)
