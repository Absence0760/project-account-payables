"""Shared pytest fixtures.

The auth + portal-auth handlers now call ``check_rate_limit`` at their
entry points, which in turn opens a Redis connection. The vast majority
of the suite is DB-free / Redis-free, so we install an in-memory
sorted-set fake under ``app.services.rate_limit.get_redis`` for every
test by default. Tests that *do* want to exercise the real limiter
(only ``test_rate_limit_security.py`` today) install their own fake via
the local ``fake_redis`` fixture and override this one.
"""

from __future__ import annotations

import pytest


class _FakeSortedSet:
    def __init__(self) -> None:
        self.store: dict[str, list[tuple[str, float]]] = {}

    def zadd(self, key, mapping) -> None:
        self.store.setdefault(key, [])
        for member, score in mapping.items():
            self.store[key].append((member, score))

    def zremrangebyscore(self, key, low, high) -> None:
        if key in self.store:
            self.store[key] = [(m, s) for m, s in self.store[key] if not (low <= s <= high)]

    def zcard(self, key) -> int:
        return len(self.store.get(key, []))

    def zrange(self, key, start, stop, withscores=False):
        items = sorted(self.store.get(key, []), key=lambda t: t[1])
        slice_ = items[start : stop + 1 if stop >= 0 else None]
        if withscores:
            return list(slice_)
        return [m for m, _ in slice_]


class _FakePipeline:
    def __init__(self, sset: _FakeSortedSet) -> None:
        self._sset = sset
        self._calls: list = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def zremrangebyscore(self, key, low, high) -> None:
        self._calls.append(("zremrangebyscore", key, low, high))

    def zadd(self, key, mapping) -> None:
        self._calls.append(("zadd", key, mapping))

    def zcard(self, key) -> None:
        self._calls.append(("zcard", key))

    def expire(self, key, ttl) -> None:
        self._calls.append(("expire", key, ttl))

    async def execute(self):
        results = []
        for call in self._calls:
            op = call[0]
            if op == "zremrangebyscore":
                self._sset.zremrangebyscore(call[1], call[2], call[3])
                results.append(None)
            elif op == "zadd":
                self._sset.zadd(call[1], call[2])
                results.append(None)
            elif op == "zcard":
                results.append(self._sset.zcard(call[1]))
            elif op == "expire":
                results.append(True)
        return results


class _FakeRedis:
    def __init__(self) -> None:
        self.sset = _FakeSortedSet()
        # `is_event_already_processed` uses `SET k v NX EX <ttl>` to dedupe
        # webhook events. The in-memory dict here is enough to make the
        # first delivery win and subsequent retries short-circuit.
        self._kv: dict[str, str] = {}

    def pipeline(self, transaction: bool = True):  # noqa: ARG002
        return _FakePipeline(self.sset)

    async def zrange(self, key, start, stop, withscores=False):
        return self.sset.zrange(key, start, stop, withscores=withscores)

    async def set(
        self,
        key: str,
        value: str,
        nx: bool = False,
        ex: int | None = None,
        **_kwargs,
    ):
        if nx and key in self._kv:
            return None
        self._kv[key] = value
        return True

    async def setex(self, key: str, ttl: int, value: str):
        # SSO state / SAML RelayState + token handoff store via SETEX. TTL is
        # not enforced in-memory; single-use is via the explicit delete.
        self._kv[key] = value
        return True

    async def get(self, key: str):
        return self._kv.get(key)

    async def exists(self, key: str) -> int:
        # Used by app.redis.is_token_blocked. Nothing is blocklisted in tests
        # unless a test sets it, so a fresh JWT is always valid.
        return 1 if key in self._kv else 0

    async def delete(self, *keys: str) -> int:
        count = 0
        for k in keys:
            if k in self._kv:
                del self._kv[k]
                count += 1
        return count


@pytest.fixture(autouse=True)
def _autouse_fake_redis(monkeypatch):
    """Stub Redis out of the rate limiter + webhook event dedup ledger
    for every test by default.

    A test that wants to exercise the real limit (mostly the dedicated
    rate-limit security tests) can ignore this fixture or override the
    target ``get_redis`` itself. Keeping the stub here means new
    rate-limited endpoints / event-deduped webhook handlers don't drag
    a Redis dependency into every otherwise-pure test file.
    """
    fake = _FakeRedis()

    async def _get_redis():
        return fake

    monkeypatch.setattr("app.services.rate_limit.get_redis", _get_redis)
    monkeypatch.setattr("app.services.webhook_security.get_redis", _get_redis)
    # MFA holds a *pending* (started-but-unverified) TOTP enrollment secret in
    # Redis so an in-flight enrollment can't disturb the factor already in
    # force. Stubbing it here means every enroll/verify test gets the ceremony
    # store for free; files that assert on the raw keyspace (test_mfa.py,
    # test_mfa_security.py) install their own fake, which wins because
    # test-requested fixtures run after autouse ones.
    monkeypatch.setattr("app.services.mfa.get_redis", _get_redis)
    # Also stub the JWT-blocklist client so authenticated requests in tests
    # don't bind a module-level real-Redis connection to one test's event loop
    # (reused on the next test's loop → "Event loop is closed"). Tests that
    # exercise the real blocklist install their own fake_redis, which overrides
    # this (test-requested fixtures run after autouse ones).
    monkeypatch.setattr("app.redis.get_redis", _get_redis)
    # verify_totp's single-use claim (issue #162) is the same class of hazard —
    # stub it here too so any test exercising the real MFA verify path doesn't
    # bind a module-level Redis connection to its event loop.
    monkeypatch.setattr("app.services.mfa.get_redis", _get_redis)
    return fake


# ---------------------------------------------------------------------------
# Real-Postgres harness (opt-in: request the ``realdb`` fixture)
#
# Most of the suite is mock-based. A handful of invariants can only be proven
# against a live Postgres + the real ASGI app: cross-tenant isolation (needs
# two real tenant DBs), the SQL WHERE filters, commit/rollback durability, and
# the role-gated HTTP endpoints. This harness provisions two persistent test
# tenants (``feoh_pytesta`` / ``feoh_pytestb``), idempotently, and truncates their
# business tables before each test. It is function-scoped with fresh engines
# per call so there are no cross-event-loop engine-reuse pitfalls.
#
# The tenant pair is claimed EXCLUSIVELY by this pytest process for the whole
# session — see ``_claim_realdb_slot`` below. Sharing it between two concurrent
# pytest processes is not survivable: the per-test reset TRUNCATEs the tenant
# tables and terminates every other backend on them, so each process would be
# deleting the other's rows and killing its connections mid-test.
#
# Requires the dev Postgres to be up (``pnpm db:up``). When it isn't reachable
# the fixture skips rather than erroring, so the mock-only suite still runs.
# ---------------------------------------------------------------------------

import asyncio  # noqa: E402
import contextlib  # noqa: E402
import os  # noqa: E402
import threading  # noqa: E402
import uuid  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402

import pytest_asyncio  # noqa: E402

# ---------------------------------------------------------------------------
# Per-process tenant slot
#
# `feoh_pytesta` / `feoh_pytestb` are process-EXCLUSIVE, not merely suite-exclusive:
# the reset below TRUNCATEs their tables and reaps every other backend on them.
# Two pytest processes pointed at the same Postgres therefore corrupt each other
# (deleted rows + `ConnectionDoesNotExistError` in unrelated files), which reads
# as flakiness rather than as the collision it is.
#
# So each process claims a *slot* — a Postgres session-level advisory lock —
# and uses the tenant pair named for it. Slot 0 keeps the historical names, so
# the overwhelmingly common single-process run (and every CI shard, each with
# its own Postgres) behaves exactly as before and creates no extra databases;
# only a second concurrent process pays for provisioning `feoh_pytesta1` etc.,
# once per session.
#
# Postgres arbitrates the claim, which is what makes this crash-safe: the lock
# is released when the holding connection goes away, so a killed run never
# strands a slot and there is nothing to garbage-collect. The connection is
# parked on its own event loop in a daemon thread so the claim can outlive the
# function-scoped loops pytest-asyncio hands each test.
# ---------------------------------------------------------------------------

# Arbitrary namespace for the two-int advisory-lock key; the second int is the
# slot number. Only this harness uses it.
_SLOT_LOCK_NAMESPACE = 0x41505453  # "APTS"


class _SlotClaim:
    """Holds an exclusive claim on one realdb tenant slot for the session."""

    def __init__(self) -> None:
        self.slot: int | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._conn = None

    def acquire(self, dsn: str) -> int:
        """Claim the lowest free slot. Raises whatever asyncpg raises if the
        server is unreachable — the caller turns that into the usual skip."""
        ready = threading.Event()
        failure: list[BaseException] = []

        def _run() -> None:
            import asyncpg

            loop = asyncio.new_event_loop()
            self._loop = loop
            asyncio.set_event_loop(loop)
            try:
                self._conn = loop.run_until_complete(asyncpg.connect(dsn))
                slot = 0
                while not loop.run_until_complete(
                    self._conn.fetchval(
                        "SELECT pg_try_advisory_lock($1, $2)", _SLOT_LOCK_NAMESPACE, slot
                    )
                ):
                    slot += 1
                self.slot = slot
            except BaseException as exc:  # noqa: BLE001 - re-raised on the main thread
                failure.append(exc)
                # The handshake may have succeeded and only the lock probe
                # failed; hand the socket back now rather than waiting for GC.
                if self._conn is not None:
                    with contextlib.suppress(Exception):
                        loop.run_until_complete(self._conn.close())
                    self._conn = None
            finally:
                ready.set()
            if not failure:
                # Park: keeps the connection (and therefore the lock) alive and
                # serviced for the rest of the session.
                loop.run_forever()
            loop.close()

        self._thread = threading.Thread(target=_run, name="realdb-slot", daemon=True)
        self._thread.start()
        ready.wait()
        if failure:
            # The thread has already torn its loop down. Drop every handle so a
            # later `release()` (or a retry from the next test, when Postgres is
            # simply down and the fixture keeps skipping) doesn't touch a closed
            # loop.
            self._loop = self._conn = self._thread = None
            raise failure[0]
        assert self.slot is not None
        return self.slot

    def release(self) -> None:
        loop, conn, thread = self._loop, self._conn, self._thread
        self._loop = self._conn = self._thread = None
        self.slot = None
        if loop is None or loop.is_closed():
            return
        # Best-effort: the run has already reported its result, and Postgres
        # drops the lock when the process exits regardless — a hung close must
        # not surface as a session-teardown error.
        if conn is not None:
            with contextlib.suppress(Exception):
                asyncio.run_coroutine_threadsafe(conn.close(), loop).result(timeout=10)
        with contextlib.suppress(Exception):
            loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(timeout=10)


_slot_claim = _SlotClaim()


def _asyncpg_dsn(url: str) -> str:
    """`postgresql+asyncpg://…` (SQLAlchemy) → `postgresql://…` (asyncpg)."""
    return url.replace("+asyncpg", "", 1)


def _claim_realdb_slot() -> int:
    """This process's realdb slot, claimed on first use and held for the session."""
    if _slot_claim.slot is None:
        from app.config import settings as cfg

        _slot_claim.acquire(_asyncpg_dsn(cfg.database_url))
    assert _slot_claim.slot is not None
    return _slot_claim.slot


# The per-test reap sweep (see the `realdb` fixture below). `pg_terminate_backend`
# is pure PID-matching against a point-in-time scan of `pg_stat_activity` — it
# does not re-verify that the target PID still belongs to the same logical
# session by the time it actually fires. Excluding `state = 'active'` means the
# sweep can only ever hit a backend that is sitting idle (or idle-in-transaction)
# — exactly the lingering-leftover-from-a-prior-test case the comment below
# describes — and can never catch a backend that is actively mid-statement,
# which is what a THIS-test connection looks like the instant it opens and
# starts its first INSERT. `state IS DISTINCT FROM 'active'` (not `<>`) so a
# NULL state (a backend that hasn't reported one yet) is also left alone rather
# than matching by SQL's three-valued-logic accident.
#
# See issue #214: `test_exception_agents.py` intermittently failed with
# `Could not refresh instance` because the sweep (as it stood before this
# filter) terminated a connection mid-`INSERT INTO purchase_orders ...`.
_REAP_STALE_BACKENDS_SQL = (
    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
    "WHERE datname = current_database() AND pid <> pg_backend_pid() "
    "AND state IS DISTINCT FROM 'active'"
)


def tenant_slugs_for_slot(slot: int) -> dict[str, str]:
    """Tenant slugs for a slot. Slot 0 keeps the historical names."""
    suffix = "" if slot == 0 else str(slot)
    return {"a": f"pytesta{suffix}", "b": f"pytestb{suffix}"}


def role_email(slug: str, role: str) -> str:
    """The seeded login for a role in a test tenant. Single source of truth —
    tests must derive it from here rather than hardcoding a slug, which changes
    with the slot."""
    return f"{role}@{slug}.test"


def pytest_sessionfinish(session, exitstatus) -> None:  # noqa: ARG001
    """Hand the slot back promptly. Postgres would release it at process exit
    anyway; doing it here keeps a long-lived runner tidy."""
    _slot_claim.release()


@dataclass
class TenantInfo:
    slug: str
    db_name: str
    org_id: uuid.UUID
    users: dict = field(default_factory=dict)  # role name -> user id


async def _ensure_test_tenants() -> dict:
    """Idempotently create the two test tenants + their role users.

    Safe to call every test: databases/tables are created once (checkfirst),
    control rows are created only if absent. Returns {key: TenantInfo}.
    """
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    # Importing the app wires every router, which transitively imports every
    # model — so Base.metadata is COMPLETE before any create_all. Without this,
    # only the models imported so far get tables, and later TRUNCATE/queries
    # reference tables that were never created.
    import app.main  # noqa: F401
    from app.api.deps import ALL_ROLES
    from app.config import settings as cfg
    from app.models import Base
    from app.models.organization import Organization
    from app.models.user import Role, User, UserRole
    from app.services.tenant_provisioning import (
        CONTROL_TABLES,
        _create_postgres_database,
        _create_tenant_tables,
    )
    from app.utils.passwords import pwd_context

    ctrl_engine = create_async_engine(cfg.database_url)
    ctrl_mk = async_sessionmaker(ctrl_engine, expire_on_commit=False)
    try:
        async with ctrl_engine.begin() as conn:
            ctrl_tables = [t for n, t in Base.metadata.tables.items() if n in CONTROL_TABLES]
            await conn.run_sync(
                lambda c: Base.metadata.create_all(c, tables=ctrl_tables, checkfirst=True)
            )

        async with ctrl_mk() as s:
            # `roles` is control-plane and NOT slotted, so on a freshly
            # initialized Postgres two concurrent pytest processes can both find
            # `admin` missing and both insert it. ON CONFLICT DO NOTHING against
            # the system-role partial unique index (`uq_roles_system_name`, org
            # id NULL) makes the seed a safe race instead of an IntegrityError
            # in whichever process commits second.
            from sqlalchemy.dialects.postgresql import insert as pg_insert

            await s.execute(
                pg_insert(Role)
                .values([{"id": uuid.uuid4(), "name": name} for name in ALL_ROLES])
                .on_conflict_do_nothing(
                    index_elements=[Role.name],
                    index_where=Role.organization_id.is_(None),
                )
            )
            await s.commit()
            # System roles only — a test's org-scoped custom role must never
            # shadow the `admin` id the seeded users are granted.
            role_ids = {
                r.name: r.id
                for r in (await s.execute(select(Role).where(Role.organization_id.is_(None))))
                .scalars()
                .all()
            }

        tenants: dict = {}
        for key, slug in tenant_slugs_for_slot(_claim_realdb_slot()).items():
            db_name = f"{cfg.tenant_db_prefix}{slug}"
            async with ctrl_mk() as s:
                org = (
                    await s.execute(select(Organization).where(Organization.slug == slug))
                ).scalar_one_or_none()
            if org is None:
                await _create_postgres_database(db_name)
                org_id = uuid.uuid4()
                async with ctrl_mk() as s:
                    s.add(
                        Organization(
                            id=org_id,
                            name=f"PyTest {slug}",
                            slug=slug,
                            plan="free",
                            db_name=db_name,
                        )
                    )
                    await s.commit()
            else:
                org_id = org.id
            # Always (re)create tenant tables — idempotent (checkfirst) and
            # backfills any table missing from a DB provisioned earlier with an
            # incomplete model metadata. Pass org_id so the Default entity is
            # seeded (multi-entity Phase 1).
            await _create_tenant_tables(db_name, organization_id=org_id)

            users: dict = {}
            async with ctrl_mk() as s:
                for role_name in ALL_ROLES:
                    email = role_email(slug, role_name)
                    u = (
                        await s.execute(select(User).where(User.email == email))
                    ).scalar_one_or_none()
                    if u is None:
                        uid = uuid.uuid4()
                        s.add(
                            User(
                                id=uid,
                                email=email,
                                full_name=role_name,
                                hashed_password=pwd_context.hash("Passw0rd!xyz"),
                                is_active=True,
                                organization_id=org_id,
                                must_change_password=False,
                            )
                        )
                        await s.flush()
                        s.add(UserRole(user_id=uid, role_id=role_ids[role_name]))
                        users[role_name] = uid
                    else:
                        users[role_name] = u.id
                await s.commit()
            tenants[key] = TenantInfo(slug=slug, db_name=db_name, org_id=org_id, users=users)

        # Control-plane reset — the counterpart of the tenant TRUNCATE below.
        # `Organization.settings` (branding, SSO, payments, …) and the partner
        # self-FK `parent_org_id` are shared mutable state on a row that
        # persists for the life of the database, so a test that writes them and
        # doesn't restore leaves every LATER run wrong, not just the rest of
        # this one. (That is a real failure we hit: `test_portal_branding`
        # stamped a product name on tenant B and `test_partner_admin`'s
        # "child has no brand yet" assertions then failed permanently, even
        # when that file was run alone.) Baseline both back to pristine here so
        # per-test isolation doesn't depend on every author remembering a
        # `finally`.
        from sqlalchemy import update

        async with ctrl_mk() as s:
            await s.execute(
                update(Organization)
                .where(Organization.id.in_([t.org_id for t in tenants.values()]))
                .values(settings={}, parent_org_id=None)
            )
            await s.commit()
        return tenants
    finally:
        await ctrl_engine.dispose()


# Every per-test harness engine uses NullPool: a connection is closed on
# release instead of parked `idle` in a pool. The realdb fixture's setup runs
# `pg_terminate_backend` on every OTHER backend on the test DB before each test
# (see that fixture) to defeat a lingering-snapshot race — but with a real pool
# that reaping can kill a connection a still-checked-in (or, under a streaming
# response, still-tearing-down) session left idle, and SQLAlchemy then hands the
# dead socket to the next operation → "connection was closed in the middle of
# operation". NullPool removes the idle-pooled connection entirely, so there is
# nothing for the reaper to kill out from under a later test. (`pool_pre_ping`
# only helps at checkout, not for a connection killed while checked in, so it
# was insufficient here.)
from sqlalchemy.pool import NullPool  # noqa: E402

_HARNESS_ENGINE_KW = {"poolclass": NullPool}


class RealDB:
    """Handle yielded by the ``realdb`` fixture.

    Gives tests fresh per-call tenant session makers, JWTs for the seeded role
    users, and an ASGI client whose DB dependencies are overridden to point at
    the test tenant (so endpoint logic runs without the module-global engines).
    """

    def __init__(self, tenants: dict) -> None:
        self.tenants = tenants
        self._engines: list = []

    def info(self, key: str) -> TenantInfo:
        return self.tenants[key]

    def email(self, key: str, role: str = "admin") -> str:
        """Seeded login for a role in a test tenant. Use this instead of writing
        `admin@pytesta.test` — the slug carries this process's slot."""
        return role_email(self.tenants[key].slug, role)

    def token(self, key: str, role: str = "admin") -> str:
        from app.api.deps import create_access_token

        info = self.tenants[key]
        return create_access_token(info.users[role], info.org_id)

    def sessionmaker(self, key: str):
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.database import _make_tenant_url

        engine = create_async_engine(
            _make_tenant_url(self.tenants[key].db_name), **_HARNESS_ENGINE_KW
        )
        self._engines.append(engine)
        return async_sessionmaker(engine, expire_on_commit=False)

    def control_sessionmaker(self):
        """Session maker for the control-plane DB (organizations, users,
        roles, email_verifications) — used by signup / provisioning tests that
        read or write control tables the tenant session makers can't reach."""
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.config import settings as cfg

        engine = create_async_engine(cfg.database_url, **_HARNESS_ENGINE_KW)
        self._engines.append(engine)
        return async_sessionmaker(engine, expire_on_commit=False)

    def client(self, *, key: str, role: str | None = "admin"):
        import httpx
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.api.deps import get_api_key_db
        from app.config import settings as cfg
        from app.database import _make_tenant_url, get_control_db
        from app.main import app
        from app.tenant import get_tenant_db

        info = self.tenants[key]
        ctrl_engine = create_async_engine(cfg.database_url, **_HARNESS_ENGINE_KW)
        tenant_engine = create_async_engine(_make_tenant_url(info.db_name), **_HARNESS_ENGINE_KW)
        self._engines += [ctrl_engine, tenant_engine]
        ctrl_mk = async_sessionmaker(ctrl_engine, expire_on_commit=False)
        tenant_mk = async_sessionmaker(tenant_engine, expire_on_commit=False)

        async def _control_db():
            async with ctrl_mk() as s:
                try:
                    yield s
                    await s.commit()
                except Exception:
                    await s.rollback()
                    raise

        async def _tenant_db():
            async with tenant_mk() as s:
                try:
                    yield s
                    await s.commit()
                except Exception:
                    await s.rollback()
                    raise

        app.dependency_overrides[get_control_db] = _control_db
        app.dependency_overrides[get_tenant_db] = _tenant_db
        # The public-API path resolves its tenant session via get_api_key_db,
        # which calls the *global* get_tenant_engine directly (production-correct,
        # single-loop). Point it at this client's per-loop harness engine too, or
        # multi-client-context tests hit a stale cross-loop engine. Auth still
        # runs — the v1 routes also depend on require_api_scope/get_api_key_principal.
        app.dependency_overrides[get_api_key_db] = _tenant_db

        headers = {"X-Tenant-Slug": info.slug}
        if role is not None:
            headers["Authorization"] = f"Bearer {self.token(key, role)}"
        transport = httpx.ASGITransport(app=app)
        return httpx.AsyncClient(transport=transport, base_url="http://test", headers=headers)

    async def cleanup(self) -> None:
        from app.main import app

        app.dependency_overrides.clear()
        for engine in self._engines:
            await engine.dispose()


@pytest_asyncio.fixture
async def realdb():
    """Function-scoped real-Postgres handle; truncates tenant data per test."""
    import asyncpg
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.database import _make_tenant_url
    from app.models import Base
    from app.services.tenant_provisioning import CONTROL_TABLES

    try:
        tenants = await _ensure_test_tenants()
    except (OSError, asyncpg.PostgresError, OperationalError) as exc:
        # The CI backend job declares Postgres as a health-checked service and
        # sets FEOH_REQUIRE_REALDB — there, an unreachable DB is a hard failure,
        # not a silent skip that would hide breakage in the real-DB tenant
        # isolation / token-rotation tests. Local runs without `pnpm db:up`
        # still skip so the mock-only suite keeps running.
        if os.environ.get("FEOH_REQUIRE_REALDB"):
            raise RuntimeError(
                "realdb fixture requires a live Postgres and FEOH_REQUIRE_REALDB is "
                f"set (CI declares Postgres as a service); refusing to skip: {exc}"
            ) from exc
        pytest.skip(f"realdb fixture requires a live Postgres (pnpm db:up): {exc}")

    tenant_tables = [f'"{n}"' for n in Base.metadata.tables if n not in CONTROL_TABLES]
    truncate = f"TRUNCATE {', '.join(tenant_tables)} RESTART IDENTITY CASCADE"
    for info in tenants.values():
        engine = create_async_engine(_make_tenant_url(info.db_name))
        try:
            async with engine.begin() as conn:
                # A prior test's request can leave a pooled backend lingering on
                # this tenant DB — `idle in transaction` (auth/RBAC raised after
                # FastAPI opened the tenant-DB session, before its `finally`
                # closed it) or `idle` but still pinning an old MVCC snapshot /
                # AccessShareLock. `cleanup()` calls `engine.dispose()`, but
                # Postgres doesn't always process the close before THIS test's
                # TRUNCATE fires next — which then either deadlocks or, worse,
                # makes the freshly-inserted rows invisible to the request under
                # test (an empty-result flake, ~1-in-3). Reaping every *other*
                # backend on the DB before the reset makes per-test isolation
                # deterministic.
                #
                # This is only safe because the slot claim (see
                # `_claim_realdb_slot`) makes this tenant pair exclusive to THIS
                # pytest process — every backend it reaps is one of our own. Do
                # not weaken that: when the pair was merely "exclusive to the
                # realdb suite", two concurrent pytest runs reaped each other,
                # surfacing as `ConnectionDoesNotExistError` in whichever
                # unrelated file happened to be mid-query.
                await conn.exec_driver_sql(_REAP_STALE_BACKENDS_SQL)
                await conn.exec_driver_sql(truncate)
                # Multi-entity (Phase 1): TRUNCATE wipes `entities` too, but every
                # tenant is expected to always have its Default entity. Restore it
                # so each test starts from the same single-entity baseline.
                await conn.execute(
                    text(
                        "INSERT INTO entities "
                        "(id, organization_id, name, slug, is_default, is_active) "
                        "VALUES (:id, :org, 'Default', 'default', true, true)"
                    ),
                    {"id": uuid.uuid4(), "org": info.org_id},
                )
        finally:
            await engine.dispose()

    db = RealDB(tenants)
    try:
        yield db
    finally:
        await db.cleanup()
        # Background-service code paths exercised by some realdb tests
        # (audit_log_shipper.ship_once, peppol_receive, contract_renewal) reach
        # the *module-global* engines in app.database — not this fixture's
        # per-test harness engines. Those globals cache an asyncpg pool bound to
        # the event loop of the first test that touches them; a later test runs
        # under a fresh function-scoped loop, and reusing the cached pool raises
        # "got Future attached to a different loop" / "another operation is in
        # progress". Dispose them after every realdb test so the next test
        # rebinds its own loop. (The per-test harness engines avoid this for the
        # request path; this covers the background-service path.)
        from app.database import dispose_all_engines

        await dispose_all_engines()
