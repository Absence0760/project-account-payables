from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

# ---------------------------------------------------------------------------
# Control-plane engine (account_payables DB — orgs, users, roles)
# ---------------------------------------------------------------------------
control_engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    pool_size=10,
    max_overflow=20,
)

control_session_factory = async_sessionmaker(control_engine, expire_on_commit=False)


async def get_control_db() -> AsyncGenerator[AsyncSession]:
    async with control_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ---------------------------------------------------------------------------
# Per-tenant engines (ap_<slug> DBs — business data)
# ---------------------------------------------------------------------------
_tenant_engines: dict[str, AsyncEngine] = {}


def _make_tenant_url(db_name: str) -> str:
    """Replace the database name in the base URL."""
    base = settings.database_url
    return base.rsplit("/", 1)[0] + "/" + db_name


def get_tenant_engine(db_name: str) -> AsyncEngine:
    if db_name not in _tenant_engines:
        _tenant_engines[db_name] = create_async_engine(
            _make_tenant_url(db_name),
            echo=settings.debug,
            pool_size=5,
            max_overflow=10,
        )
    return _tenant_engines[db_name]


async def dispose_all_engines() -> None:
    await control_engine.dispose()
    for engine in _tenant_engines.values():
        await engine.dispose()
    _tenant_engines.clear()
