"""Every index a migration creates is also declared on its model — the systemic
guard for migration 0093.

Why this file exists
--------------------
A database in this project is built two ways, and only one of them runs Alembic:

* an EXISTING tenant / control plane gets ``alembic upgrade head``;
* a NEW tenant gets ``Base.metadata.create_all``
  (``services/tenant_provisioning._create_tenant_tables``), and so does the
  control plane on a fresh install and in this very test harness.

``create_all`` builds exactly what the ORM declares. An index written into a
migration and never declared on its model therefore reaches migrated databases
and *silently never reaches a freshly-provisioned one* — permanently different
schemas, with nothing in the system noticing: reads still return correct rows,
just by sequential scan, and where the index is UNIQUE the invariant it enforces
is simply absent on half the fleet.

Round 24 found ONE instance (``ix_audit_log_shipped_at_null``, migration 0010)
and fixed it in migration 0092. An audit of all 216 ``CREATE INDEX`` statements
across every revision found **twenty** — including two partial UNIQUE indexes,
i.e. correctness rather than performance:

* ``uq_positive_pay_run_format`` — the only concurrency backstop under
  ``POST /api/positive-pay/payment-runs/{id}/check-issue``'s read-then-insert;
* ``uq_subscription_one_live_per_org`` — "at most one live subscription per
  org", the billing invariant.

Eighteen were genuinely missing and are now declared on their models (migration
0093 restates the CREATEs so an already-provisioned database catches up). Two
were not missing at all — the same index under another name, or superseded by a
stronger one — and are reconciled in 0093 instead of duplicated onto a model;
they are the entries in ``EXEMPT`` below.

What this file pins
-------------------
1. **Opt-out, not opt-in.** Every ``CREATE [UNIQUE] INDEX`` in every revision,
   for every table still in ``Base.metadata``, must be declared on the model. A
   NEW migration-only index fails this suite the moment it lands; it does not
   need to be added to any list first.
2. **An exemption needs a written reason, and stays honest.** Each ``EXEMPT``
   entry is re-checked: the index must still be created by a migration and must
   still be undeclared, so a stale exemption fails rather than rotting.
3. **The two exemptions' reasons are true**, checked structurally rather than
   taken on trust — each is covered by a model-declared twin on the same
   column(s) with the same predicate.
4. **The two builds produce identical indexes** — proven on a real Postgres by
   dropping what ``create_all`` built and re-creating it from migration 0093's
   own SQL, then comparing ``pg_get_indexdef`` output. Same name is not enough;
   this compares columns, order, direction, partial predicate and access method
   (the pgvector HNSW index included).
5. **The unique pre-flight SQL is valid**, because a typo in it would otherwise
   surface for the first time during a real migration on a real fleet.

Real-Postgres harness (``realdb``) for 4 and 5; 1-3 need no database.
"""

from __future__ import annotations

import ast
import importlib.util
import re
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import text

from app.models.base import Base

VERSIONS_DIR = Path(__file__).resolve().parent.parent / "alembic" / "versions"
_MIGRATION_PATH = VERSIONS_DIR / "0093_migration_only_indexes.py"

TENANT = "a"


def _migration_module():
    """Import 0093 for its declarations (it never touches ``op`` at import)."""
    spec = importlib.util.spec_from_file_location("_mig_0093", _MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MIGRATION = _migration_module()
#: The 18 indexes 0093 ENSURES exist (their own revisions still own them).
ENSURED: list[tuple[str, str, str]] = MIGRATION._ENSURED
UNIQUE_NAMES: frozenset[str] = MIGRATION._UNIQUE_INDEX_NAMES


# ---------------------------------------------------------------------------
# Exemptions — opt-out, each with the reason it is not a defect.
# ---------------------------------------------------------------------------

#: An index a migration creates that is deliberately NOT declared on its model.
#: Adding an entry here is a claim that ``create_all`` not building it is
#: correct — so say why, and expect
#: ``test_every_exemption_is_still_a_live_exemption`` to fail the moment the
#: claim stops being true.
EXEMPT: dict[str, str] = {
    "ix_bank_transactions_matched_payment": (
        "Superseded, not missing. Migration 0019 built it as "
        "(matched_payment_id) WHERE matched_payment_id IS NOT NULL — the exact "
        "column and predicate of uq_bank_transactions_matched_payment, the "
        "UNIQUE index migration 0081 added and BankTransaction declares. A "
        "unique index serves every read the non-unique one could, so declaring "
        "this on the model would build a second, redundant index on a hot "
        "table. Migration 0093 drops it instead (downgrade recreates it)."
    ),
    "ix_vendor_change_requests_org_id": (
        "A second name for an index the model already declares. Migration 0022 "
        "named it ix_vendor_change_requests_org_id; index=True on "
        "VendorChangeRequest.organization_id makes SQLAlchemy name the same "
        "single-column index ix_vendor_change_requests_organization_id. "
        "Declaring both would build the index twice. Migration 0093 converges "
        "on the model's name — creating it where absent and dropping this alias "
        "— so the two provisioning paths agree exactly rather than 'agree "
        "except for a name'."
    ),
}


# ---------------------------------------------------------------------------
# The scanner
# ---------------------------------------------------------------------------

_CREATE_INDEX = re.compile(
    r"CREATE\s+(?P<unique>UNIQUE\s+)?INDEX\s+(?:CONCURRENTLY\s+)?"
    r"(?:IF\s+NOT\s+EXISTS\s+)?(?P<name>[A-Za-z_]\w*)\s+ON\s+(?P<body>.+)",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class MigrationIndex:
    name: str
    table: str
    unique: bool
    body: str
    revision: str


def _string_literals(source: str) -> list[str]:
    """Every string literal in a module, with implicit concatenation resolved.

    Parsing rather than regexing the raw text is what makes this reliable: the
    migrations spell their DDL across several adjacent string literals, which
    ``ast`` joins into one ``Constant`` for us. f-strings are flattened to their
    literal parts (enough to see the CREATE INDEX head, which is never
    interpolated).
    """
    out: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.append(node.value)
        elif isinstance(node, ast.JoinedStr):
            out.append(
                "".join(
                    part.value
                    for part in node.values
                    if isinstance(part, ast.Constant) and isinstance(part.value, str)
                )
            )
    return out


def _scan_migrations() -> dict[str, MigrationIndex]:
    """Every index any revision creates, keyed by index name.

    Keyed by name because an index name is globally unique within a Postgres
    schema — two revisions creating the same name are creating the same index
    (0093 restating what 0004 built, say), and the first definition wins.
    """
    found: dict[str, MigrationIndex] = {}
    for path in sorted(VERSIONS_DIR.glob("*.py")):
        for literal in _string_literals(path.read_text()):
            match = _CREATE_INDEX.search(literal)
            if match is None:
                continue
            body = " ".join(match.group("body").split())
            table = re.split(r"[\s(]", body, maxsplit=1)[0]
            found.setdefault(
                match.group("name"),
                MigrationIndex(
                    name=match.group("name"),
                    table=table,
                    unique=bool(match.group("unique")),
                    body=body,
                    revision=path.name,
                ),
            )
    return found


MIGRATION_INDEXES = _scan_migrations()


def _declared_names(table: str) -> set[str]:
    """Index names ``create_all`` would build for a table.

    ``Index(...)`` objects and named ``UniqueConstraint``s both become a
    Postgres index, and either spelling satisfies a migration's CREATE.
    """
    from sqlalchemy import UniqueConstraint

    meta_table = Base.metadata.tables[table]
    names = {index.name for index in meta_table.indexes if index.name}
    names |= {
        constraint.name
        for constraint in meta_table.constraints
        if isinstance(constraint, UniqueConstraint) and constraint.name
    }
    return names


# ---------------------------------------------------------------------------
# 1-3. the declarations themselves — no database needed
# ---------------------------------------------------------------------------


def test_the_scanner_actually_finds_the_migrations_indexes():
    """A scanner that silently matched nothing would make every assertion below
    pass vacuously — which is precisely the failure mode this whole file exists
    to prevent one level down."""
    assert len(MIGRATION_INDEXES) > 200, len(MIGRATION_INDEXES)
    # A known partial index, a known UNIQUE one, and one from the newest
    # revision — spot checks that the ast+regex pair survives all three
    # spellings the migrations use.
    assert MIGRATION_INDEXES["ix_audit_log_shipped_at_null"].table == "audit_log"
    assert MIGRATION_INDEXES["uq_positive_pay_run_format"].unique
    assert MIGRATION_INDEXES["ix_invoice_embeddings_embedding_hnsw"].revision.startswith("0003")


@pytest.mark.parametrize(
    "index_name",
    sorted(
        name
        for name, index in MIGRATION_INDEXES.items()
        if index.table in Base.metadata.tables and name not in EXEMPT
    ),
)
def test_every_migration_index_is_declared_on_its_model(index_name):
    """Opt-out, not opt-in: a NEW migration-only index fails here on the day it
    lands, without anyone having to remember to register it.

    If this fails for an index you just added: declare it in the owning model's
    ``__table_args__`` (``Index(...)``, with ``unique=True`` /
    ``postgresql_where=`` / ``postgresql_using=`` to match the migration
    exactly). If ``create_all`` genuinely should not build it, add it to
    ``EXEMPT`` above with the reason.
    """
    index = MIGRATION_INDEXES[index_name]
    declared = _declared_names(index.table)
    assert index_name in declared, (
        f"{index.revision} creates {index_name} on {index.table}, but the model "
        f"declares only {sorted(declared)}. A create_all-provisioned database "
        f"(every NEW tenant, and the control plane on a fresh install) would "
        f"never get it. Migration definition: CREATE "
        f"{'UNIQUE ' if index.unique else ''}INDEX {index_name} ON {index.body}"
    )


@pytest.mark.parametrize("index_name", sorted(EXEMPT))
def test_every_exemption_is_still_a_live_exemption(index_name):
    """An exemption that stopped being true is worse than no exemption — it
    hides the case it was written to explain. Both halves of the claim are
    re-checked: the index is still created by a migration, and is still not
    declared on the model."""
    assert index_name in MIGRATION_INDEXES, (
        f"{index_name} is exempted but no migration creates it any more — delete the EXEMPT entry."
    )
    index = MIGRATION_INDEXES[index_name]
    assert index.table in Base.metadata.tables, (
        f"{index.table} is no longer a model table — delete the EXEMPT entry."
    )
    assert index_name not in _declared_names(index.table), (
        f"{index_name} IS declared on {index.table} now — delete the EXEMPT "
        "entry so the parity check covers it."
    )
    assert len(EXEMPT[index_name]) > 80, "an exemption needs a real reason, not a label"


def test_the_superseded_bank_index_is_covered_by_its_unique_twin():
    """``ix_bank_transactions_matched_payment``'s exemption claims
    ``uq_bank_transactions_matched_payment`` covers it. Checked, not assumed:
    same column, same partial predicate, and unique."""
    twin = next(
        index
        for index in Base.metadata.tables["bank_transactions"].indexes
        if index.name == "uq_bank_transactions_matched_payment"
    )
    assert [column.name for column in twin.columns] == ["matched_payment_id"]
    assert twin.unique
    predicate = str(twin.dialect_options["postgresql"]["where"])
    assert predicate == "matched_payment_id IS NOT NULL"

    superseded = MIGRATION_INDEXES["ix_bank_transactions_matched_payment"]
    assert "matched_payment_id" in superseded.body
    assert "WHERE matched_payment_id IS NOT NULL" in superseded.body
    # And 0093 removes it rather than leaving both on a migrated tenant.
    assert any(
        statement == "DROP INDEX IF EXISTS ix_bank_transactions_matched_payment"
        for _table, statement in MIGRATION._UPGRADE
    )


def test_the_renamed_vendor_change_index_is_the_same_index_under_the_models_name():
    """``ix_vendor_change_requests_org_id``'s exemption claims the model's
    ``ix_vendor_change_requests_organization_id`` is the same index. Checked:
    same single column, neither partial, neither unique — and 0093 converges the
    fleet on the model's name."""
    twin = next(
        index
        for index in Base.metadata.tables["vendor_change_requests"].indexes
        if index.name == "ix_vendor_change_requests_organization_id"
    )
    assert [column.name for column in twin.columns] == ["organization_id"]
    assert not twin.unique
    assert twin.dialect_options["postgresql"]["where"] is None

    alias = MIGRATION_INDEXES["ix_vendor_change_requests_org_id"]
    assert alias.body.replace(" ", "") == "vendor_change_requests(organization_id)"
    assert not alias.unique

    upgrade = [statement for _table, statement in MIGRATION._UPGRADE]
    create_at = upgrade.index(
        "CREATE INDEX IF NOT EXISTS ix_vendor_change_requests_organization_id "
        "ON vendor_change_requests (organization_id)"
    )
    drop_at = upgrade.index("DROP INDEX IF EXISTS ix_vendor_change_requests_org_id")
    # Create before drop: the column is never left unindexed, not even briefly.
    assert create_at < drop_at


def test_0093_ensures_but_never_drops_the_indexes_it_does_not_own():
    """Same ``_ADOPTED`` semantics migration 0092 established: restating an
    earlier revision's CREATE (idempotently) is a catch-up for an
    already-provisioned database, so reverting 0093 must not remove migration
    0004's / 0013's / 0048's index."""
    dropped = " ".join(statement for _table, statement in MIGRATION._DOWNGRADE)
    for _table, name, _body in ENSURED:
        assert name not in dropped, f"downgrade would drop {name}, which 0093 does not own"
    # Everything the downgrade DOES touch, the upgrade actually changed.
    assert {statement for _t, statement in MIGRATION._DOWNGRADE} == {
        "CREATE INDEX IF NOT EXISTS ix_bank_transactions_matched_payment "
        "ON bank_transactions (matched_payment_id) WHERE matched_payment_id IS NOT NULL",
        "CREATE INDEX IF NOT EXISTS ix_vendor_change_requests_org_id "
        "ON vendor_change_requests (organization_id)",
    }


def test_0093_ddl_is_idempotent_and_never_concurrent():
    """``CREATE INDEX CONCURRENTLY`` cannot run inside Alembic's transaction,
    and combined with ``IF NOT EXISTS`` it is a trap: a cancelled build leaves an
    INVALID index whose name makes every later run skip it and report success.
    Argued in the migration's docstring; pinned here so it is re-argued rather
    than drifted into."""
    for _table, statement in MIGRATION._UPGRADE + MIGRATION._DOWNGRADE:
        assert "CONCURRENTLY" not in statement.upper()
        assert statement.startswith("CREATE INDEX IF NOT EXISTS ") or statement.startswith(
            ("CREATE UNIQUE INDEX IF NOT EXISTS ", "DROP INDEX IF EXISTS ")
        ), statement


def test_0093_spans_both_databases_and_gates_on_the_table():
    """Unlike 0092 this revision touches control-plane AND tenant tables, so the
    per-table existence gate is what lets one revision serve both."""
    from app.services.tenant_provisioning import CONTROL_TABLES

    tables = {table for table, _name, _body in ENSURED}
    assert tables & CONTROL_TABLES == {"organizations", "users", "subscriptions"}
    assert tables - CONTROL_TABLES, "the tenant half went missing"
    import inspect

    assert "information_schema.tables" in inspect.getsource(MIGRATION._table_exists)
    assert "_table_exists(table)" in inspect.getsource(MIGRATION._run)


def test_every_ensured_index_is_marked_unique_exactly_as_its_migration_spells_it():
    """``_UNIQUE_INDEX_NAMES`` decides which restatement gets ``UNIQUE``. Getting
    that wrong would silently install a NON-unique index under a ``uq_`` name —
    a correctness control that looks present and enforces nothing."""
    for _table, name, _body in ENSURED:
        origin = MIGRATION_INDEXES[name]
        assert (name in UNIQUE_NAMES) == origin.unique, (
            f"{name}: 0093 says unique={name in UNIQUE_NAMES}, "
            f"{origin.revision} says unique={origin.unique}"
        )


# ---------------------------------------------------------------------------
# 4 + 5. against real Postgres — do the two builds agree?
# ---------------------------------------------------------------------------


async def _present_tables(session) -> set[str]:
    rows = await session.execute(
        text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
    )
    return set(rows.scalars().all())


async def _index_defs(session) -> dict[str, str]:
    rows = await session.execute(
        text("SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'")
    )
    return {name: definition for name, definition in rows.all()}


async def _assert_builds_agree(session) -> int:
    """Drop what ``create_all`` built, rebuild from 0093's own SQL, compare.

    Nothing is committed — the caller rolls back — so this leaves the harness
    database exactly as it found it. Comparing ``pg_indexes.indexdef`` compares
    what Postgres actually built: access method, column list, per-column sort
    direction, operator class, and the partial predicate.
    """
    present = await _present_tables(session)
    applicable = [(t, n, b) for t, n, b in ENSURED if t in present]
    assert applicable, "no ensured index applies to this database"

    before = await _index_defs(session)
    missing = [name for _t, name, _b in applicable if name not in before]
    assert not missing, (
        f"create_all did not build {sorted(missing)} — the model declaration is "
        "missing or spelled differently"
    )

    for _table, name, _body in applicable:
        await session.execute(text(f"DROP INDEX {name}"))
    for table, statement in MIGRATION._UPGRADE:
        if table in present and statement.startswith("CREATE"):
            await session.execute(text(statement))

    after = await _index_defs(session)
    for _table, name, _body in applicable:
        assert name in after, f"the migration did not rebuild {name}"
        assert after[name] == before[name], (
            f"{name}: create_all built\n  {before[name]}\nmigration 0093 built\n  {after[name]}"
        )
    return len(applicable)


async def test_create_all_and_the_migration_build_identical_tenant_indexes(realdb):
    async with realdb.sessionmaker(TENANT)() as session:
        try:
            count = await _assert_builds_agree(session)
        finally:
            await session.rollback()
    # 15 tenant-side; the other three are control-plane.
    assert count == 15, count


async def test_create_all_and_the_migration_build_identical_control_plane_indexes(realdb):
    async with realdb.control_sessionmaker()() as session:
        try:
            count = await _assert_builds_agree(session)
        finally:
            await session.rollback()
    assert count == 3, count


async def test_the_unique_preflight_sql_runs_and_reports_clean(realdb):
    """A typo in the pre-flight would surface for the first time mid-migration,
    on a real fleet. Run each statement against a real schema instead."""
    async with realdb.sessionmaker(TENANT)() as tenant_session:
        tenant_tables = await _present_tables(tenant_session)
        async with realdb.control_sessionmaker()() as control_session:
            control_tables = await _present_tables(control_session)
            checked = 0
            for table, sql, remedy in MIGRATION._UNIQUE_PREFLIGHT:
                assert remedy.strip(), f"{table}: the operator needs a remedy, not a bare count"
                if table in tenant_tables:
                    session, present = tenant_session, tenant_tables
                elif table in control_tables:
                    session, present = control_session, control_tables
                else:  # pragma: no cover - both harness DBs carry every model table
                    pytest.fail(f"{table} exists in neither harness database")
                assert table in present
                assert (await session.execute(text(sql))).scalar() == 0
                checked += 1
    assert checked == len(MIGRATION._UNIQUE_PREFLIGHT) == len(UNIQUE_NAMES)


async def test_a_freshly_provisioned_tenant_carries_every_ensured_index(realdb):
    """The end state the whole revision is for: a ``create_all``-built tenant is
    no longer distinguishable from a migrated one."""
    async with realdb.sessionmaker(TENANT)() as session:
        present = await _present_tables(session)
        defs = await _index_defs(session)
    expected = {name for table, name, _body in ENSURED if table in present}
    assert expected <= set(defs), sorted(expected - set(defs))
    # And the two reconciled names have converged on the model's spelling.
    assert "ix_bank_transactions_matched_payment" not in defs
    assert "ix_vendor_change_requests_org_id" not in defs
    assert "ix_vendor_change_requests_organization_id" in defs
    assert "uq_bank_transactions_matched_payment" in defs
