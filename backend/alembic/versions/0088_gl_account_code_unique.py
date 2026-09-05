"""GL account codes are unique within the chart they belong to (tenant).

A GL code must resolve to exactly ONE account. Two rows answering to one code
make "which account was this invoice coded to?" unanswerable — invoices carry
the code as a STRING, not an FK — and every consumer treats a code as a set
member (``gl_recode._ActiveChart``, the AI extraction GL catalog, bulk-recode
validation), so the second row is invisible until someone reconciles the GL.
Nothing enforced this: ``POST /api/gl-accounts`` did no duplicate check and the
table carried no constraint.

**Why two partial indexes instead of one three-column unique.**
``gl_accounts.entity_id`` is the deliberate exception to multi-entity's backfill
(``docs/multi-entity.md`` § Chart of accounts): NULL means the account is
**shared across every entity**, not "unstamped legacy row" as it does on
invoices / vendors. NULLs never compare equal in a unique index, so a plain
``UNIQUE (organization_id, entity_id, code)`` would enforce nothing at all on
the shared chart — the exact place a duplicate is most damaging, since a shared
account is in every entity's effective chart. Splitting on the NULL-ness of
``entity_id`` states the two rules separately and enforces both:

* ``uq_gl_accounts_org_shared_code``  — one SHARED row per ``(org, code)``
* ``uq_gl_accounts_org_entity_code``  — one row per ``(org, entity, code)``

A shared ``6000`` and an entity's own ``6000`` still coexist: that is an entity
**overriding** the shared account, and the effective chart ``shared ∪ own``
resolves to one row per entity because ``api/gl_accounts._sync_match_query``
prefers the entity's own.

**Pre-existing duplicates FAIL the migration, loudly.** A unique index cannot be
built over dirty data, and unlike migration 0081's over-claimed bank
transactions there is no conservative automatic repair here: two rows sharing a
code differ in ``name`` / ``account_type`` / ``parent_code`` / ``is_active``,
invoices already reference the code as free text, and picking a survivor is a
chart-of-accounts decision an operator makes, not one a migration guesses.
Deleting the loser would silently discard chart configuration; merging would
silently pick one row's ``name`` for spend already coded under the other. So the
upgrade raises with the offending ``(organization_id, entity_id, code)`` groups
named (GL codes are org configuration, not PII) and the tenant's upgrade stops
before anything is changed. Remediation: reconcile the duplicates in that
tenant — keep one row per code per chart, deactivating or deleting the rest —
then re-run ``alembic upgrade head`` / ``scripts/migrate_all_tenants.py``.

Revision ID: 0088_gl_account_code_unique
Revises: 0087_cash_plans
Create Date: 2026-09-04

TENANT DB ONLY: ``gl_accounts`` is tenant-scoped (it is NOT in
``tenant_provisioning.CONTROL_TABLES``). The upgrade is gated on the table
existing, so the revision no-ops on the control plane and fans out to every
tenant DB via ``scripts/migrate_all_tenants.py`` (or
``FEOH_MIGRATE_TENANT=feoh_<slug> alembic upgrade head`` for one). Fresh tenants
get both indexes from ``create_all`` in ``tenant_provisioning`` — they are
declared on ``app.models.gl_account.GLAccount``.

Idempotent + reversible: ``CREATE UNIQUE INDEX IF NOT EXISTS`` /
``DROP INDEX IF EXISTS``, and the duplicate pre-check is a read.
"""

from sqlalchemy import text

from alembic import op

revision = "0088_gl_account_code_unique"
down_revision = "0087_cash_plans"
branch_labels = None
depends_on = None

# NULL ``entity_id`` values group together under GROUP BY (unlike under a unique
# index), so this one query catches duplicates in the shared chart AND in any
# entity's own chart.
DUPLICATE_SQL = """
    SELECT organization_id, entity_id, code, count(*) AS n
    FROM gl_accounts
    GROUP BY organization_id, entity_id, code
    HAVING count(*) > 1
    ORDER BY count(*) DESC, code
"""

#: Executed in order; each is independently idempotent.
INDEX_STATEMENTS = (
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_gl_accounts_org_shared_code "
    "ON gl_accounts (organization_id, code) WHERE entity_id IS NULL",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_gl_accounts_org_entity_code "
    "ON gl_accounts (organization_id, entity_id, code) WHERE entity_id IS NOT NULL",
)

DROP_STATEMENTS = (
    "DROP INDEX IF EXISTS uq_gl_accounts_org_shared_code",
    "DROP INDEX IF EXISTS uq_gl_accounts_org_entity_code",
)

# How many offending groups to name before truncating the message.
_MAX_REPORTED = 20


def _is_tenant_db() -> bool:
    bind = op.get_bind()
    return (
        bind.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'gl_accounts'"
            )
        ).scalar()
        is not None
    )


def format_duplicate_error(rows: list) -> str:
    """PII-free, actionable message naming the duplicate GL codes."""
    shown = rows[:_MAX_REPORTED]
    lines = [
        f"  code={code!r} entity_id={'SHARED' if entity_id is None else entity_id} rows={n}"
        for _org, entity_id, code, n in shown
    ]
    if len(rows) > len(shown):
        lines.append(f"  ... and {len(rows) - len(shown)} more")
    return (
        "gl_accounts holds duplicate codes, so the uniqueness indexes this "
        "migration installs cannot be created. Nothing has been changed. "
        "Reconcile the chart of accounts in this tenant — keep ONE row per code "
        "per chart (the shared chart, and each entity's own), deactivating or "
        "deleting the rest — then re-run the migration.\n" + "\n".join(lines)
    )


def upgrade() -> None:
    if not _is_tenant_db():
        return

    bind = op.get_bind()
    duplicates = list(bind.execute(text(DUPLICATE_SQL)))
    if duplicates:
        raise RuntimeError(format_duplicate_error(duplicates))

    for statement in INDEX_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    if not _is_tenant_db():
        return
    for statement in DROP_STATEMENTS:
        op.execute(statement)
