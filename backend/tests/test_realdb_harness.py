"""Guards on the realdb harness's own isolation contract (`tests/conftest.py`).

The harness gives every realdb test a clean tenant pair. Two properties make
that true, and both used to be assumed rather than enforced — each failure was
silent and looked like flakiness, so they get explicit coverage here:

1. **The tenant pair is exclusive to this pytest process.** The per-test reset
   TRUNCATEs the tenant tables and `pg_terminate_backend`s every other backend
   on them. Two concurrent pytest runs sharing `ap_pytesta` therefore deleted
   each other's rows and killed each other's connections, surfacing as
   `asyncpg.ConnectionDoesNotExistError` in whatever unrelated file happened to
   be mid-query. Exclusivity now comes from a Postgres session-level advisory
   lock (a "slot") that names the databases.

2. **Shared control-plane state is reset too.** `Organization.settings` and the
   partner `parent_org_id` live on a row that outlives the whole session, so a
   test that wrote them and didn't restore corrupted every LATER run, not just
   the rest of that one.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from app.models.organization import Organization
from tests.conftest import (
    _SLOT_LOCK_NAMESPACE,
    _asyncpg_dsn,
    _claim_realdb_slot,
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
