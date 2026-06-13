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
    # Also stub the JWT-blocklist client so authenticated requests in tests
    # don't bind a module-level real-Redis connection to one test's event loop
    # (reused on the next test's loop → "Event loop is closed"). Tests that
    # exercise the real blocklist install their own fake_redis, which overrides
    # this (test-requested fixtures run after autouse ones).
    monkeypatch.setattr("app.redis.get_redis", _get_redis)
    return fake


# ---------------------------------------------------------------------------
# Real-Postgres harness (opt-in: request the ``realdb`` fixture)
#
# Most of the suite is mock-based. A handful of invariants can only be proven
# against a live Postgres + the real ASGI app: cross-tenant isolation (needs
# two real tenant DBs), the SQL WHERE filters, commit/rollback durability, and
# the role-gated HTTP endpoints. This harness provisions two persistent test
# tenants (``ap_pytesta`` / ``ap_pytestb``), idempotently, and truncates their
# business tables before each test. It is function-scoped with fresh engines
# per call so there are no cross-event-loop engine-reuse pitfalls.
#
# Requires the dev Postgres to be up (``pnpm db:up``). When it isn't reachable
# the fixture skips rather than erroring, so the mock-only suite still runs.
# ---------------------------------------------------------------------------

import os  # noqa: E402
import uuid  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402

import pytest_asyncio  # noqa: E402

_TEST_TENANTS = {"a": "pytesta", "b": "pytestb"}


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
            existing = {r.name: r.id for r in (await s.execute(select(Role))).scalars().all()}
            for name in ALL_ROLES:
                if name not in existing:
                    rid = uuid.uuid4()
                    s.add(Role(id=rid, name=name))
                    existing[name] = rid
            await s.commit()
            role_ids = {r.name: r.id for r in (await s.execute(select(Role))).scalars().all()}

        tenants: dict = {}
        for key, slug in _TEST_TENANTS.items():
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
                    email = f"{role_name}@{slug}.test"
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
        return tenants
    finally:
        await ctrl_engine.dispose()


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

    def token(self, key: str, role: str = "admin") -> str:
        from app.api.deps import create_access_token

        info = self.tenants[key]
        return create_access_token(info.users[role], info.org_id)

    def sessionmaker(self, key: str):
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.database import _make_tenant_url

        engine = create_async_engine(_make_tenant_url(self.tenants[key].db_name))
        self._engines.append(engine)
        return async_sessionmaker(engine, expire_on_commit=False)

    def control_sessionmaker(self):
        """Session maker for the control-plane DB (organizations, users,
        roles, email_verifications) — used by signup / provisioning tests that
        read or write control tables the tenant session makers can't reach."""
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.config import settings as cfg

        engine = create_async_engine(cfg.database_url)
        self._engines.append(engine)
        return async_sessionmaker(engine, expire_on_commit=False)

    def client(self, *, key: str, role: str | None = "admin"):
        import httpx
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.config import settings as cfg
        from app.database import _make_tenant_url, get_control_db
        from app.main import app
        from app.tenant import get_tenant_db

        info = self.tenants[key]
        ctrl_engine = create_async_engine(cfg.database_url)
        tenant_engine = create_async_engine(_make_tenant_url(info.db_name))
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
        # sets AP_REQUIRE_REALDB — there, an unreachable DB is a hard failure,
        # not a silent skip that would hide breakage in the real-DB tenant
        # isolation / token-rotation tests. Local runs without `pnpm db:up`
        # still skip so the mock-only suite keeps running.
        if os.environ.get("AP_REQUIRE_REALDB"):
            raise RuntimeError(
                "realdb fixture requires a live Postgres and AP_REQUIRE_REALDB is "
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
                # test (an empty-result flake, ~1-in-3). These `ap_pytest{a,b}`
                # DBs are exclusive to the (sequential) realdb suite, so reaping
                # every *other* backend on the DB before the reset is safe and
                # makes per-test isolation deterministic.
                await conn.exec_driver_sql(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND pid <> pg_backend_pid()"
                )
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
