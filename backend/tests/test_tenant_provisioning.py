"""Tests for the tenant-provisioning service.

Covers the path shared by ``scripts/create_tenant.py`` and the
``/api/signup/complete`` endpoint (``app/services/tenant_provisioning.py``):

* slug -> ``ap_<slug>`` database-name mapping
* the CONTROL_TABLES / tenant-tables split (so a tenant DB never gets
  control-plane tables, and every tenant table fans out)
* ``_create_postgres_database`` idempotency / duplicate-slug handling
  (mocked asyncpg — no DB needed)
* end-to-end ``provision_tenant`` against a live Postgres (realdb): org +
  admin user creation, password hashing via the shared bcrypt_sha256
  context, admin-role grant, ``must_change_password`` flag, real tenant
  tables created, and re-running for the same slug being idempotent.

The realdb tests create + drop their own throwaway tenant DB (the realdb
fixture only manages the two fixed ``pytesta``/``pytestb`` tenants), so they
clean up the control-plane org/user rows and the Postgres database they make.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from app.config import settings
from app.models import Base
from app.models.organization import Organization
from app.models.user import Role, User, UserRole
from app.services.tenant_provisioning import (
    CONTROL_TABLES,
    _create_postgres_database,
    provision_tenant,
)
from app.utils.passwords import pwd_context

# ---------------------------------------------------------------------------
# Pure / structural — slug mapping + the control/tenant table split
# ---------------------------------------------------------------------------


def test_control_tables_are_the_expected_control_plane_set():
    # These live in the control plane DB and must never be created inside a
    # tenant DB. A regression here would either leak control tables into tenant
    # DBs or (if a tenant table were added) silently drop a tenant table from
    # provisioning.
    assert CONTROL_TABLES == frozenset(
        {
            "organizations",
            "users",
            "roles",
            "user_roles",
            "email_verifications",
            "assistant_usage",
            "api_keys",
        }
    )


def test_control_tables_exist_in_metadata():
    # Guard against a rename: every name in CONTROL_TABLES must be a real table.
    all_tables = set(Base.metadata.tables)
    assert CONTROL_TABLES <= all_tables


def test_tenant_table_set_excludes_control_and_is_nonempty():
    tenant_tables = {n for n in Base.metadata.tables if n not in CONTROL_TABLES}
    # Spot-check that core tenant tables fan out and control tables don't.
    assert "invoices" in tenant_tables
    assert "vendors" in tenant_tables
    assert "payments" in tenant_tables
    # PEPPOL transmission log is tenant-scoped — fans out to every tenant DB.
    assert "peppol_transmissions" in tenant_tables
    assert tenant_tables.isdisjoint(CONTROL_TABLES)
    assert "organizations" not in tenant_tables
    assert "users" not in tenant_tables


def test_db_name_uses_prefix_and_slug():
    # The slug -> ap_<slug> mapping is the multi-tenancy invariant. Provisioning
    # derives db_name as f"{settings.tenant_db_prefix}{slug}".
    assert settings.tenant_db_prefix == "ap_"
    slug = "newco"
    assert f"{settings.tenant_db_prefix}{slug}" == "ap_newco"


# ---------------------------------------------------------------------------
# _create_postgres_database — idempotency / duplicate-slug (mocked asyncpg)
# ---------------------------------------------------------------------------


async def test_create_postgres_database_creates_when_absent():
    conn = AsyncMock()
    # pg_database lookup returns no row -> CREATE DATABASE should run.
    conn.fetchval = AsyncMock(return_value=None)
    conn.execute = AsyncMock()
    conn.close = AsyncMock()

    with patch(
        "app.services.tenant_provisioning.asyncpg.connect",
        new=AsyncMock(return_value=conn),
    ):
        await _create_postgres_database("ap_freshslug")

    # Exactly one CREATE DATABASE for the requested name.
    assert conn.execute.await_count == 1
    created_sql = conn.execute.await_args.args[0]
    assert "CREATE DATABASE" in created_sql
    assert "ap_freshslug" in created_sql
    conn.close.assert_awaited_once()


async def test_create_postgres_database_idempotent_when_present():
    # Re-provisioning the same slug must NOT issue a second CREATE DATABASE —
    # this is the duplicate-slug safety on the DB-creation step.
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=1)  # pg_database row already exists
    conn.execute = AsyncMock()
    conn.close = AsyncMock()

    with patch(
        "app.services.tenant_provisioning.asyncpg.connect",
        new=AsyncMock(return_value=conn),
    ):
        await _create_postgres_database("ap_existing")

    conn.execute.assert_not_called()
    conn.close.assert_awaited_once()


async def test_create_postgres_database_closes_connection_on_error():
    # The finally: must close the asyncpg connection even when the existence
    # probe blows up, so we don't leak a connection to the maintenance DB.
    conn = AsyncMock()
    conn.fetchval = AsyncMock(side_effect=RuntimeError("boom"))
    conn.close = AsyncMock()

    with patch(
        "app.services.tenant_provisioning.asyncpg.connect",
        new=AsyncMock(return_value=conn),
    ):
        with pytest.raises(RuntimeError):
            await _create_postgres_database("ap_explodes")

    conn.close.assert_awaited_once()


async def test_create_postgres_database_parses_host_port_from_url():
    # _create_postgres_database parses host/port/user/password out of the
    # configured asyncpg URL and connects to the "postgres" maintenance DB.
    # Assert it threads those parsed values through verbatim.
    captured: dict = {}

    async def _fake_connect(**kwargs):
        captured.update(kwargs)
        c = MagicMock()
        c.fetchval = AsyncMock(return_value=1)
        c.execute = AsyncMock()
        c.close = AsyncMock()
        return c

    test_url = "postgresql+asyncpg://apuser:apsecret@db.example.com:6543/account_payables"
    with patch.object(settings, "database_url", test_url):
        with patch(
            "app.services.tenant_provisioning.asyncpg.connect",
            new=_fake_connect,
        ):
            await _create_postgres_database("ap_parsetest")

    assert captured["host"] == "db.example.com"
    assert captured["port"] == 6543
    assert captured["user"] == "apuser"
    assert captured["password"] == "apsecret"
    # Always connects to the maintenance DB, never the target tenant DB.
    assert captured["database"] == "postgres"


async def test_create_postgres_database_url_parse_defaults_port_5432():
    captured: dict = {}

    async def _fake_connect(**kwargs):
        captured.update(kwargs)
        c = MagicMock()
        c.fetchval = AsyncMock(return_value=1)
        c.execute = AsyncMock()
        c.close = AsyncMock()
        return c

    # No explicit port in the URL -> default 5432.
    test_url = "postgresql+asyncpg://u:p@localhost/account_payables"
    with patch.object(settings, "database_url", test_url):
        with patch(
            "app.services.tenant_provisioning.asyncpg.connect",
            new=_fake_connect,
        ):
            await _create_postgres_database("ap_defaultport")

    assert captured["host"] == "localhost"
    assert captured["port"] == 5432


# ---------------------------------------------------------------------------
# End-to-end provision_tenant against a live Postgres (realdb)
# ---------------------------------------------------------------------------


async def _control_mk():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(settings.database_url)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _drop_database(db_name: str) -> None:
    """Drop a tenant DB we created in a test (mirrors the asyncpg URL parsing
    in ``_create_postgres_database``)."""
    import asyncpg

    url = settings.database_url.replace("postgresql+asyncpg://", "")
    userpass, hostdb = url.split("@", 1)
    user, password = userpass.split(":", 1)
    host_port, _ = hostdb.rsplit("/", 1)
    if ":" in host_port:
        host, port_str = host_port.split(":", 1)
        port = int(port_str)
    else:
        host, port = host_port, 5432
    conn = await asyncpg.connect(
        host=host, port=port, user=user, password=password, database="postgres"
    )
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
    finally:
        await conn.close()


async def _cleanup_org(slug: str) -> None:
    """Delete the control-plane org + its users + the tenant DB for a slug."""
    engine, mk = await _control_mk()
    db_name = f"{settings.tenant_db_prefix}{slug}"
    try:
        async with mk() as s:
            org = (
                await s.execute(select(Organization).where(Organization.slug == slug))
            ).scalar_one_or_none()
            if org is not None:
                users = (
                    (await s.execute(select(User).where(User.organization_id == org.id)))
                    .scalars()
                    .all()
                )
                for u in users:
                    await s.execute(UserRole.__table__.delete().where(UserRole.user_id == u.id))
                    await s.delete(u)
                await s.delete(org)
                await s.commit()
    finally:
        await engine.dispose()
    await _drop_database(db_name)


@pytest.fixture
def throwaway_slug():
    # Slug that survives slug-format rules (starts with a letter, lowercase,
    # hyphen-only) and is unique per run so concurrent/repeat runs don't clash.
    slug = "prov" + uuid.uuid4().hex[:8]
    return slug


@pytest_asyncio.fixture
async def _provision_on_test_loop():
    """Bind provision_tenant's control_session_factory to an engine created on
    THIS test's event loop.

    The module-global factory's engine binds to whichever loop first touched it
    (the first provisioning test), so subsequent tests on fresh per-test loops
    otherwise fail with 'attached to a different loop'. We patch in a fresh
    factory and dispose its engine after the test.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import app.services.tenant_provisioning as tp

    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    with patch.object(tp, "control_session_factory", factory):
        yield
    await engine.dispose()


async def test_provision_tenant_creates_org_user_and_tenant_tables(
    realdb, throwaway_slug, _provision_on_test_loop
):
    slug = throwaway_slug
    db_name = f"{settings.tenant_db_prefix}{slug}"
    try:
        result = await provision_tenant(
            company_name="Throwaway Corp",
            slug=slug,
            admin_email=f"admin@{slug}.test",
            admin_name="Throwaway Admin",
            admin_password="Sup3rSecret!pw",
        )

        # Result carries the slug -> ap_<slug> mapping.
        assert result.db_name == db_name
        assert isinstance(result.organization_id, uuid.UUID)
        assert isinstance(result.user_id, uuid.UUID)

        engine, mk = await _control_mk()
        try:
            async with mk() as s:
                org = (
                    await s.execute(
                        select(Organization).where(Organization.id == result.organization_id)
                    )
                ).scalar_one()
                assert org.slug == slug
                assert org.db_name == db_name
                assert org.name == "Throwaway Corp"
                assert org.plan == "free"

                user = (await s.execute(select(User).where(User.id == result.user_id))).scalar_one()
                assert user.email == f"admin@{slug}.test"
                assert user.full_name == "Throwaway Admin"
                assert user.is_active is True
                assert user.organization_id == result.organization_id
                # Default forces a password change on first login.
                assert user.must_change_password is True
                # Password is hashed (never stored plaintext) and verifies via
                # the shared bcrypt_sha256 context.
                assert user.hashed_password != "Sup3rSecret!pw"
                assert pwd_context.verify("Sup3rSecret!pw", user.hashed_password)

                # The admin role was granted (the realdb control plane seeds the
                # RBAC roles, so provision_tenant attaches the user to "admin").
                admin_role = (
                    await s.execute(select(Role).where(Role.name == "admin"))
                ).scalar_one()
                ur_count = (
                    await s.execute(
                        select(func.count())
                        .select_from(UserRole)
                        .where(
                            UserRole.user_id == result.user_id,
                            UserRole.role_id == admin_role.id,
                        )
                    )
                ).scalar_one()
                assert ur_count == 1
        finally:
            await engine.dispose()

        # The tenant DB exists and the tenant tables fanned out (migrations
        # equivalent: create_all over every non-control table).
        from sqlalchemy.ext.asyncio import create_async_engine

        from app.database import _make_tenant_url

        tenant_engine = create_async_engine(_make_tenant_url(db_name))
        try:
            async with tenant_engine.connect() as conn:
                for tbl in ("invoices", "vendors", "payments", "entities", "peppol_transmissions"):
                    present = await conn.exec_driver_sql(f"SELECT to_regclass('{tbl}')")
                    assert present.scalar() is not None, f"tenant table {tbl} missing"
                # The PEPPOL idempotency guard + message-id dedupe must be built
                # by create_all on a fresh tenant — identical to the migration.
                peppol_indexes = await conn.exec_driver_sql(
                    "SELECT indexname FROM pg_indexes WHERE tablename = 'peppol_transmissions'"
                )
                built = {row[0] for row in peppol_indexes}
                assert "uq_peppol_one_live_per_invoice_direction" in built
                assert "uq_peppol_message_id" in built
                # And a control-plane table must NOT have been created here.
                orgs_here = await conn.exec_driver_sql("SELECT to_regclass('organizations')")
                assert orgs_here.scalar() is None
                # Multi-entity: provisioning seeds exactly one Default entity,
                # owned by the new org, and the business tables carry entity_id.
                default_count = await conn.exec_driver_sql(
                    "SELECT count(*) FROM entities WHERE is_default"
                )
                assert default_count.scalar() == 1
                default_org = await conn.exec_driver_sql(
                    "SELECT organization_id FROM entities WHERE is_default"
                )
                assert default_org.scalar() == result.organization_id
                has_col = await conn.exec_driver_sql(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'invoices' AND column_name = 'entity_id'"
                )
                assert has_col.scalar() == 1
        finally:
            await tenant_engine.dispose()
    finally:
        await _cleanup_org(slug)


async def test_provision_tenant_respects_must_change_password_false(
    realdb, throwaway_slug, _provision_on_test_loop
):
    slug = throwaway_slug
    try:
        result = await provision_tenant(
            company_name="Internal Co",
            slug=slug,
            admin_email=f"admin@{slug}.test",
            admin_name="Internal Admin",
            admin_password="Sup3rSecret!pw",
            plan="enterprise",
            must_change_password=False,
        )
        engine, mk = await _control_mk()
        try:
            async with mk() as s:
                org = (
                    await s.execute(
                        select(Organization).where(Organization.id == result.organization_id)
                    )
                ).scalar_one()
                assert org.plan == "enterprise"
                user = (await s.execute(select(User).where(User.id == result.user_id))).scalar_one()
                assert user.must_change_password is False
        finally:
            await engine.dispose()
    finally:
        await _cleanup_org(slug)


async def test_provision_tenant_duplicate_slug_raises_and_keeps_one_org(
    realdb, throwaway_slug, _provision_on_test_loop
):
    slug = throwaway_slug
    try:
        await provision_tenant(
            company_name="First Co",
            slug=slug,
            admin_email=f"first@{slug}.test",
            admin_name="First Admin",
            admin_password="Sup3rSecret!pw",
        )

        # Re-provisioning the same slug must fail (Organization.slug is unique,
        # Organization.db_name is unique) — provisioning is NOT silently
        # idempotent at the org level, it surfaces the collision.
        with pytest.raises(Exception):
            await provision_tenant(
                company_name="Second Co",
                slug=slug,
                admin_email=f"second@{slug}.test",
                admin_name="Second Admin",
                admin_password="Sup3rSecret!pw",
            )

        # Exactly one org for the slug survived (the first one); the failed
        # second attempt left no partial org row.
        engine, mk = await _control_mk()
        try:
            async with mk() as s:
                count = (
                    await s.execute(
                        select(func.count())
                        .select_from(Organization)
                        .where(Organization.slug == slug)
                    )
                ).scalar_one()
                assert count == 1
                org = (
                    await s.execute(select(Organization).where(Organization.slug == slug))
                ).scalar_one()
                assert org.name == "First Co"
        finally:
            await engine.dispose()
    finally:
        await _cleanup_org(slug)


async def _database_exists(db_name: str) -> bool:
    import asyncpg

    from app.services.tenant_provisioning import _parse_maintenance_dsn

    conn = await asyncpg.connect(**_parse_maintenance_dsn())
    try:
        return bool(await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", db_name))
    finally:
        await conn.close()


async def test_provision_tenant_drops_orphan_db_when_provisioning_fails(
    throwaway_slug, _provision_on_test_loop
):
    """If the control-plane insert / tenant-table step fails AFTER the database
    was created, provision_tenant drops the orphan DB so a partial failure
    doesn't leak databases or squat the slug's namespace."""
    import app.services.tenant_provisioning as tp

    slug = throwaway_slug
    db_name = f"{settings.tenant_db_prefix}{slug}"

    async def _boom(**_kwargs):
        raise RuntimeError("simulated provisioning failure after DB creation")

    with patch.object(tp, "_provision_into", _boom):
        with pytest.raises(RuntimeError):
            await provision_tenant(
                company_name="Doomed Co",
                slug=slug,
                admin_email=f"admin@{slug}.test",
                admin_name="Doomed Admin",
                admin_password="Sup3rSecret!pw",
            )

    # The database created at the start of provisioning must have been dropped.
    assert await _database_exists(db_name) is False


# ---------------------------------------------------------------------------
# Role-name uniqueness (the constraint provision_tenant's role lookup relies on)
# ---------------------------------------------------------------------------


async def test_duplicate_system_role_name_is_rejected(realdb):
    """Two system roles (organization_id NULL) with the same name are rejected
    by ``uq_roles_system_name``. Without it, a re-seeded control plane could
    accumulate duplicate ``admin`` rows and ``provision_tenant``'s
    ``scalar_one_or_none()`` role lookup raised ``MultipleResultsFound``."""
    from sqlalchemy.exc import IntegrityError

    engine, mk = await _control_mk()
    try:
        async with mk() as s:
            # "admin" already exists as a system role in the seeded control
            # plane — a second one must violate the partial unique index.
            s.add(Role(name="admin", description="dup system admin"))
            with pytest.raises(IntegrityError):
                await s.flush()
            await s.rollback()
    finally:
        await engine.dispose()


async def test_org_scoped_roles_unique_within_org_not_across(realdb):
    """Org-scoped custom roles are unique only within an org: two different
    orgs may both define ``Approver`` (``uq_roles_org_name`` keys on
    ``(organization_id, name)``), but one org cannot define it twice."""
    from sqlalchemy.exc import IntegrityError

    engine, mk = await _control_mk()
    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    try:
        async with mk() as s:
            # Same name across two different orgs is allowed — flush succeeds.
            s.add_all(
                [
                    Role(name="Approver", organization_id=org_a),
                    Role(name="Approver", organization_id=org_b),
                ]
            )
            await s.flush()
            # A second "Approver" in org_a violates uq_roles_org_name.
            s.add(Role(name="Approver", organization_id=org_a))
            with pytest.raises(IntegrityError):
                await s.flush()
            # Roll back everything — the cross-org pair was only flushed, never
            # committed, so this test persists nothing.
            await s.rollback()
    finally:
        await engine.dispose()
