"""Guards on the realdb harness's own isolation contract (`tests/conftest.py`).

The harness gives every realdb test a clean tenant pair. Two properties make
that true, and both used to be assumed rather than enforced — each failure was
silent and looked like flakiness, so they get explicit coverage here:

1. **The tenant pair is exclusive to this pytest process.** The per-test reset
   TRUNCATEs the tenant tables and `pg_terminate_backend`s every other backend
   on them. Two concurrent pytest runs sharing `feoh_pytesta` therefore deleted
   each other's rows and killed each other's connections, surfacing as
   `asyncpg.ConnectionDoesNotExistError` in whatever unrelated file happened to
   be mid-query. Exclusivity now comes from a Postgres session-level advisory
   lock (a "slot") that names the databases.

2. **Shared control-plane state is reset too.** `Organization.settings` and the
   partner `parent_org_id` live on a row that outlives the whole session, so a
   test that wrote them and didn't restore corrupted every LATER run, not just
   the rest of that one.

3. **The reap sweep never kills a backend mid-statement (issue #214).**
   `pg_terminate_backend` is pure PID-matching against a point-in-time scan of
   `pg_stat_activity` — it doesn't re-verify that the target PID still belongs
   to the same logical session by the time it fires. Without a `state`
   exclusion, the sweep could (and did) occasionally terminate a connection
   that was actively mid-`INSERT`, surfacing as
   `sqlalchemy.exc.InvalidRequestError: Could not refresh instance` in
   `test_exception_agents.py`. The sweep now excludes `state = 'active'`.
"""

from __future__ import annotations

import asyncio
import contextlib

import asyncpg
import pytest
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from app.database import _make_tenant_url
from app.models.organization import Organization
from tests.conftest import (
    _REAP_STALE_BACKENDS_SQL,
    _REBUILT_TENANT_DBS,
    _SLOT_LOCK_NAMESPACE,
    _asyncpg_dsn,
    _base_control_db_name,
    _claim_realdb_slot,
    _ensure_test_tenants,
    control_db_name_for_slot,
    role_email,
    tenant_slugs_for_slot,
)

# `asyncio_mode = "auto"` (pyproject) runs the async tests below — no marks.


# ── Slot → database naming (pure) ─────────────────────────────────────


def test_slot_zero_keeps_the_historical_tenant_names():
    """Slot 0 must stay `pytesta`/`pytestb` so the common single-process run
    (and every CI shard, each with its own Postgres) provisions nothing new."""
    assert tenant_slugs_for_slot(0) == {"a": "pytesta", "b": "pytestb"}


def test_each_slot_names_a_disjoint_tenant_pair():
    """Distinct slots must never resolve to a shared database — that sharing IS
    the cross-process defect."""
    seen: set[str] = set()
    for slot in range(4):
        slugs = set(tenant_slugs_for_slot(slot).values())
        assert not (slugs & seen), f"slot {slot} reuses a tenant from a lower slot"
        seen |= slugs


def test_role_email_is_derived_from_the_slug():
    assert role_email("pytesta3", "admin") == "admin@pytesta3.test"


# ── The control-plane DB is per-slot too (issue #251 bucket 2) ────────
#
# Unlike the tenant pair — where slot 0 reuses the historical unsuffixed
# names so the common single-process run provisions nothing new — the
# control-plane database must NEVER be the real shared default
# (`settings.database_url`, i.e. `feohledger`), not even for slot 0. Sharing
# it was the actual defect: a locally-running dev backend's background
# sweeps (e.g. `extraction_reaper.run_reaper_loop`) enumerate every
# `Organization` row in that database with no filter, so a harness org
# living there was discoverable — and mutable — by a process this harness
# never claimed exclusivity from. See docs/known-issues.md § "A dev backend
# on the same Postgres mutates the pytest tenant DBs mid-test".


def test_control_db_name_for_slot_never_reuses_the_shared_default():
    base = _base_control_db_name()
    for slot in range(4):
        name = control_db_name_for_slot(slot)
        assert name != base, f"slot {slot} resolved to the shared default control-plane DB"
        assert name.startswith(f"{base}_pytest")


def test_each_slot_names_a_disjoint_control_db():
    seen: set[str] = set()
    for slot in range(4):
        name = control_db_name_for_slot(slot)
        assert name not in seen, f"slot {slot} reuses a control-plane DB from a lower slot"
        seen.add(name)


async def test_control_sessionmaker_points_at_this_slot_s_own_db(realdb):
    """`RealDB.control_sessionmaker()` (and, by the same construction, the
    `client()` control-DB override) must resolve to THIS slot's own
    control-plane database, never the shared default."""
    expected_name = control_db_name_for_slot(_claim_realdb_slot())
    async with realdb.control_sessionmaker()() as s:
        bind = s.get_bind()
        assert bind.url.database == expected_name


async def test_default_control_plane_cannot_see_a_harness_organization(realdb):
    """The headline regression: a session on the DEFAULT control-plane
    connection string (`settings.database_url` — exactly what a locally
    running dev backend uses, e.g. via `pnpm dev:backend`) must not be able
    to see an `Organization` row this harness created for its slot.

    Before this fix, every slot's test orgs lived in that same shared
    `feohledger` database, so a dev backend's background sweeps discovered
    and mutated them mid-test — the confirmed root cause in
    docs/known-issues.md. This is a raw column check against
    `settings.database_url` directly (not the ORM `Organization` mapping, and
    not `realdb.control_sessionmaker()`, which now deliberately points
    elsewhere) so the proof doesn't depend on the default database's schema
    matching current model metadata — the two are now free to diverge, which
    is itself part of what this fix buys.
    """
    from app.config import settings as cfg

    org_id = realdb.info("a").org_id
    conn = await asyncpg.connect(_asyncpg_dsn(cfg.database_url))
    try:
        found = await conn.fetchval("SELECT 1 FROM organizations WHERE id = $1", org_id)
        assert found is None, (
            "a session on the DEFAULT control-plane DB could see the harness's "
            "org row — the per-slot control-plane DB isolation is broken"
        )
    finally:
        await conn.close()


# ── The claim actually excludes another session ───────────────────────


async def test_another_session_cannot_take_this_process_s_slot(realdb):
    """The headline: a *second* Postgres session — the stand-in for a second
    pytest process — is refused this process's slot and is handed a free one.

    Advisory locks are session-scoped, so a second connection is a faithful
    proxy for a second process.
    """
    import asyncpg

    from app.config import settings as cfg

    slot = _claim_realdb_slot()
    conn = await asyncpg.connect(_asyncpg_dsn(cfg.database_url))
    try:
        held = await conn.fetchval(
            "SELECT pg_try_advisory_lock($1, $2)", _SLOT_LOCK_NAMESPACE, slot
        )
        assert held is False, "a second session took the slot this process holds"

        # …and it is not simply locked out of everything: a free slot is
        # available, which is what lets a concurrent run proceed on its own
        # databases. Probe far above the range real processes claim (they count
        # up from 0) so this assertion can't itself collide with a concurrent
        # pytest run holding the next slot.
        probe = 10_000
        free = await conn.fetchval(
            "SELECT pg_try_advisory_lock($1, $2)", _SLOT_LOCK_NAMESPACE, probe
        )
        assert free is True
        await conn.fetchval("SELECT pg_advisory_unlock($1, $2)", _SLOT_LOCK_NAMESPACE, probe)
    finally:
        await conn.close()


async def test_harness_tenants_match_this_process_s_slot(realdb):
    """The databases the harness hands out are the ones the claim named."""
    expected = tenant_slugs_for_slot(_claim_realdb_slot())
    assert {k: realdb.info(k).slug for k in ("a", "b")} == expected
    assert realdb.info("a").db_name.endswith(expected["a"])
    assert realdb.email("a", "admin") == role_email(expected["a"], "admin")


# ── The reap sweep spares a backend mid-statement (issue #214) ────────


async def test_reap_sweep_terminates_idle_backend_but_spares_active_one(realdb):
    """Direct regression for the `Could not refresh instance` flake.

    Simulates exactly the collision that hit `test_exception_agents.py`: one
    backend sits genuinely idle (the thing the sweep exists to clean up) while
    another is actively mid-statement (what a fresh test connection looks like
    the instant it opens). Runs the real production sweep SQL — not a
    reimplementation — from a third connection and asserts the idle one is
    killed while the active one's statement completes untouched.
    """
    dsn = _asyncpg_dsn(_make_tenant_url(realdb.info("a").db_name))

    idle_conn = await asyncpg.connect(dsn)
    active_conn = await asyncpg.connect(dsn)
    reaper_conn = await asyncpg.connect(dsn)
    try:
        active_query = asyncio.ensure_future(active_conn.execute("SELECT pg_sleep(1.5)"))
        await asyncio.sleep(0.3)  # let both backends register their state

        await reaper_conn.execute(_REAP_STALE_BACKENDS_SQL)

        # The idle leftover is exactly what the sweep targets — it must be
        # gone. asyncpg surfaces a server-initiated termination either as a
        # PostgresError/OSError from the driver, or (once the protocol state
        # machine has already been knocked over by the termination) as its own
        # InternalClientError on the next call — any of them proves the
        # backend is dead, which is the property under test.
        with pytest.raises(Exception):  # noqa: B017, PT011 — see comment above
            await idle_conn.fetchval("SELECT 1")

        # The mid-statement connection must survive: its query completes
        # normally, not with a server-side termination.
        await active_query
        assert await active_conn.fetchval("SELECT 1") == 1
    finally:
        for c in (idle_conn, active_conn, reaper_conn):
            with contextlib.suppress(Exception):
                await c.close()


# ── Schema drift is healed at session start (issue #219) ─────────────


async def test_schema_drift_is_rebuilt_on_first_ensure_of_a_session(realdb):
    """Direct regression for issue #219.

    The tenant pair is long-lived across pytest invocations, and
    `create_all(checkfirst=True)` creates missing tables but never adds a
    missing column to an existing one — so a DB predating a later model change
    stayed silently stale forever (the real incident: 68 failures on
    `expenses.converted_currency` after migration 0076). The harness now
    rebuilds the physical schema from the current metadata once per session.

    Simulates the drift by dropping that same column, then clears the
    once-per-session marker so the next `_ensure_test_tenants` call behaves
    like the first of a fresh pytest session. The ensure must restore the
    column, and must reinstall what goes down with the schema — the audit_log
    immutability triggers prove the rebuild went through the real
    `_create_tenant_tables` path, not a bare `create_all`.
    """
    dsn = _asyncpg_dsn(_make_tenant_url(realdb.info("a").db_name))

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("ALTER TABLE expenses DROP COLUMN converted_currency")
    finally:
        await conn.close()

    _REBUILT_TENANT_DBS.clear()
    await _ensure_test_tenants()  # re-adds both DBs to the marker set

    conn = await asyncpg.connect(dsn)
    try:
        col = await conn.fetchval(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'expenses' AND column_name = 'converted_currency'"
        )
        assert col == 1, "session-start rebuild did not restore the dropped column"
        triggers = await conn.fetchval(
            "SELECT count(*) FROM pg_trigger "
            "WHERE tgname IN ('audit_log_no_delete', 'audit_log_no_update')"
        )
        assert triggers == 2, "audit_log immutability triggers missing after the rebuild"
    finally:
        await conn.close()


# ── Control-plane reset between tests ─────────────────────────────────
#
# Definition order is the run order: the first test dirties the shared org row
# exactly as `test_portal_branding` legitimately does, the second asserts the
# harness handed it back clean.


async def test_leak_control_plane_org_settings(realdb):
    async with realdb.control_sessionmaker()() as s:
        org = (
            await s.execute(select(Organization).where(Organization.id == realdb.info("b").org_id))
        ).scalar_one()
        org.settings = {"brand": {"product_name": "Leaked Brand"}}
        org.parent_org_id = realdb.info("a").org_id
        flag_modified(org, "settings")
        await s.commit()


async def test_control_plane_org_state_is_reset_for_the_next_test(realdb):
    async with realdb.control_sessionmaker()() as s:
        org = (
            await s.execute(select(Organization).where(Organization.id == realdb.info("b").org_id))
        ).scalar_one()
    assert (org.settings or {}) == {}, "settings leaked from the previous test"
    assert org.parent_org_id is None, "parent_org_id leaked from the previous test"
