from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack

from fastapi import Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings

# ---------------------------------------------------------------------------
# Commit-before-response (read-after-write durability)
# ---------------------------------------------------------------------------
# FastAPI unwinds a `Depends(yield)` dependency's post-yield code from an
# AsyncExitStack it only exits AFTER `await response(scope, receive, send)` —
# i.e. after the client already holds its 201. A session that commits in its
# teardown therefore acknowledges a write before making it durable, and a fast
# enough follow-up read can miss the row it was just told exists.
#
# FastAPI unwinds a second, inner stack under this scope key BEFORE sending, so
# a commit registered there lands on the correct side of the response.
# See docs/decisions.md §20 and docs/known-issues.md.
_FUNCTION_ASTACK_KEY = "fastapi_function_astack"


def commit_before_response(session: AsyncSession, request: Request | None) -> bool:
    """Arrange for ``session`` to commit *before* the response is sent.

    Returns ``True`` when the hook was registered. ``False`` means the request
    has no usable FastAPI exit stack — a WebSocket, a non-HTTP caller, or a
    FastAPI internals change. The caller's post-yield commit still runs in that
    case, so the worst outcome is the pre-existing late-commit behaviour, never
    a lost write. `tests/test_commit_before_response.py` guards the key.
    """
    stack = request.scope.get(_FUNCTION_ASTACK_KEY) if request is not None else None
    if not isinstance(stack, AsyncExitStack):
        return False

    async def _commit_on_success(exc_type: object, exc: object, tb: object) -> bool:
        # Success path only — an in-flight exception must reach the caller's
        # post-yield `rollback()` with nothing committed behind its back.
        if exc_type is None and session.in_transaction():
            await session.commit()
        return False  # never suppress

    stack.push_async_exit(_commit_on_success)
    return True


# ---------------------------------------------------------------------------
# Control-plane engine (feohledger DB — orgs, users, roles)
# ---------------------------------------------------------------------------
control_engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    pool_size=10,
    max_overflow=20,
)

control_session_factory = async_sessionmaker(control_engine, expire_on_commit=False)


async def get_control_db(request: Request) -> AsyncGenerator[AsyncSession]:
    async with control_session_factory() as session:
        commit_before_response(session, request)
        try:
            yield session
            # Backstop. Normally a no-op: the pre-send hook already committed
            # and nothing has re-opened a transaction. It still matters for
            # writes made after the response starts (a streaming body) and for
            # any request where the hook could not be registered.
            if session.in_transaction():
                await session.commit()
        except Exception:
            await session.rollback()
            raise


# ---------------------------------------------------------------------------
# Per-tenant engines (feoh_<slug> DBs — business data)
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
