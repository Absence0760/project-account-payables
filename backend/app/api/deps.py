"""Shared FastAPI dependencies for auth, tenant context, and DB sessions."""

import logging
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import Depends, Header, HTTPException, Request, status
from jose import JWTError, jwt
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_control_db, get_tenant_engine
from app.models.api_key import ApiKey
from app.models.organization import Organization
from app.models.user import User
from app.redis import is_token_blocked

logger = logging.getLogger(__name__)

ALGORITHM = "HS256"

# Role constants — mirror the four roles seeded into `roles` table by
# `scripts/seed.py`. Centralised here so a typo doesn't silently lock
# everyone out (`require_roles("admni")` would be a 403 for everyone).
ROLE_ADMIN = "admin"
ROLE_AP_MANAGER = "ap_manager"
ROLE_AP_CLERK = "ap_clerk"
ROLE_CFO = "cfo"

ALL_ROLES = (ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_AP_CLERK, ROLE_CFO)


def create_access_token(user_id: uuid.UUID, org_id: uuid.UUID) -> str:
    token, _jti = create_access_token_with_jti(user_id, org_id)
    return token


def create_access_token_with_jti(user_id: uuid.UUID, org_id: uuid.UUID) -> tuple[str, str]:
    """Mint an access token and return both the encoded JWT and the JTI.

    Callers that need to track the session in Redis (login, MFA verify, SSO
    callback) use this variant so they can register the JTI without having
    to re-decode the token.
    """
    jti = str(uuid.uuid4())
    expire = datetime.now(UTC) + timedelta(minutes=settings.access_token_expire_minutes)
    payload = {
        "sub": str(user_id),
        "org": str(org_id),
        "typ": "user",
        "jti": jti,
        "exp": expire,
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM), jti


def create_vendor_access_token(vendor_user_id: uuid.UUID, vendor_id: uuid.UUID) -> str:
    """Mint a supplier-portal JWT. `typ=vendor` is the gate that stops a vendor
    JWT from resolving through `get_current_user` (and vice versa)."""
    jti = str(uuid.uuid4())
    expire = datetime.now(UTC) + timedelta(minutes=settings.access_token_expire_minutes)
    payload = {
        "sub": str(vendor_user_id),
        "ven": str(vendor_id),
        "typ": "vendor",
        "jti": jti,
        "exp": expire,
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    """Decode a JWT and return the payload. Raises on invalid tokens."""
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


async def get_current_user(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_control_db),
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    token = authorization.removeprefix("Bearer ")
    payload = decode_token(token)

    # Reject vendor-portal JWTs — they resolve through `get_current_vendor_user`
    # and must not acquire an AP-app User session by mistake.
    if payload.get("typ") == "vendor":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    # Check blocklist
    jti = payload.get("jti")
    if jti and await is_token_blocked(jti):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has been revoked"
        )

    result = await db.execute(
        select(User).where(User.id == user_id).options(selectinload(User.roles))
    )
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


def get_org_id(user: User = Depends(get_current_user)) -> uuid.UUID:
    return user.organization_id


def require_roles(*allowed: str):
    """Dependency factory — restricts an endpoint to users holding ANY of the
    given roles. 403 on miss.

    Usage::

        @router.post("/users")
        async def create_user(
            body: CreateUserRequest,
            user: User = Depends(require_roles(ROLE_ADMIN)),
        ):
            ...

    The check is "any-of," not "all-of." A user with multiple roles passes if
    they hold at least one of the listed ones — matches the way the frontend
    role gates work (`hasAnyRole`). For tighter checks, compose two
    dependencies or call `user.roles` directly inside the handler.

    Misses are logged at WARNING with the request method/path, the actor, and
    the role mismatch. They're attack-shaped events — flooding the log on 403
    is intentional so monitoring picks up brute-force probing.
    """
    if not allowed:
        raise ValueError("require_roles() needs at least one role")
    unknown = set(allowed) - set(ALL_ROLES)
    if unknown:
        # Catch typos at import time, not at request time.
        raise ValueError(f"Unknown role(s) in require_roles: {sorted(unknown)}")

    allowed_set = frozenset(allowed)

    async def checker(
        request: Request,
        user: User = Depends(get_current_user),
    ) -> User:
        held = {r.name for r in user.roles}
        if held & allowed_set:
            return user
        logger.warning(
            "RBAC denied: user=%s org=%s roles=%s required_any=%s %s %s",
            user.id,
            user.organization_id,
            sorted(held),
            sorted(allowed_set),
            request.method,
            request.url.path,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your role does not permit this action.",
        )

    return checker


# ---------------------------------------------------------------------------
# Programmatic API-key authentication (public /api/v1 surface).
#
# Separate from the JWT Bearer path above: a key authenticates an *org's*
# machine-to-machine access via the `X-API-Key` header, resolves to its
# organization, and the org resolves to its tenant DB via the existing tenant
# chokepoint (`get_tenant_engine(org.db_name)`). There is no X-Tenant-Slug on
# this surface — the key IS the tenant boundary. See `backend/docs/public-api.md`.
# ---------------------------------------------------------------------------

# Single non-enumerating 401 for every api-key failure (missing header,
# unknown prefix, bad digest, revoked key). Distinct messages would let a
# caller probe which keys/prefixes exist.
_API_KEY_401 = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid API key",
    headers={"WWW-Authenticate": "ApiKey"},
)


@dataclass(frozen=True)
class ApiKeyPrincipal:
    """The resolved identity behind a validated `X-API-Key` request."""

    api_key_id: uuid.UUID
    organization_id: uuid.UUID
    db_name: str
    scopes: tuple[str, ...]


async def get_api_key_principal(
    x_api_key: str | None = Header(default=None),
    db: AsyncSession = Depends(get_control_db),
) -> ApiKeyPrincipal:
    """Authenticate a programmatic request from the `X-API-Key` header.

    Resolves prefix → row by indexed prefix → constant-time sha256 compare →
    reject if missing / revoked. On success records `last_used_at` best-effort.
    Every failure raises the same opaque 401 so the response can't enumerate
    keys or tenants. The org's tenant DB is resolved (and a session opened) by
    `get_api_key_db` below, which depends on this.
    """
    # Local import keeps the token primitives out of the module import graph
    # of every consumer of deps.py.
    from app.services.api_keys import (
        constant_time_equals,
        hash_api_key,
        key_prefix_of,
    )

    # Platform kill switch — when off, the surface fails closed with the same
    # opaque 401 (never a distinct "feature disabled" that would confirm the
    # endpoint exists).
    if not settings.public_api_enabled:
        raise _API_KEY_401
    if not x_api_key:
        raise _API_KEY_401
    presented = x_api_key.strip()
    if not presented:
        raise _API_KEY_401

    prefix = key_prefix_of(presented)
    result = await db.execute(select(ApiKey).where(ApiKey.key_prefix == prefix))
    candidates = result.scalars().all()

    presented_hash = hash_api_key(presented)
    matched: ApiKey | None = None
    for candidate in candidates:
        # Constant-time digest compare — never short-circuit on the first
        # differing byte. Revocation is checked after a hash match so a revoked
        # key and a wrong key are indistinguishable from the outside.
        if constant_time_equals(candidate.key_hash, presented_hash):
            matched = candidate
            break
    if matched is None or matched.revoked_at is not None:
        raise _API_KEY_401

    org = await db.get(Organization, matched.organization_id)
    if org is None:
        raise _API_KEY_401

    # Best-effort last-used stamp on the SAME request-scoped control session.
    # A separate session (own connection) would run a 2nd concurrent operation
    # on the control engine's pooled asyncpg connection — asyncpg forbids that
    # ("another operation is in progress") and it poisons the connection for the
    # rest of the request. Sequential reuse of `db` is safe. Never break auth.
    try:
        await db.execute(
            update(ApiKey).where(ApiKey.id == matched.id).values(last_used_at=datetime.now(UTC))
        )
        await db.commit()
    except Exception as exc:  # pragma: no cover - observability, not auth
        logger.warning("api-key last_used stamp failed: id=%s err=%s", matched.id, exc)
        await db.rollback()

    return ApiKeyPrincipal(
        api_key_id=matched.id,
        organization_id=org.id,
        db_name=org.db_name,
        scopes=tuple(matched.scopes or ()),
    )


async def get_api_key_db(
    principal: ApiKeyPrincipal = Depends(get_api_key_principal),
) -> AsyncGenerator[AsyncSession]:
    """Yield a session bound to the API key's org tenant DB.

    Reuses the same `get_tenant_engine` chokepoint as the JWT path's
    `get_tenant_db`, so programmatic reads are tenant-isolated at the data
    layer — never a hand-rolled engine, never a hardcoded `ap_<slug>` name.
    """
    engine = get_tenant_engine(principal.db_name)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def require_api_scope(scope: str):
    """Dependency factory — require a scope on the authenticating API key.

    This slice only mints `read` keys, but gating each route on the scope it
    needs means a future write surface inherits enforcement for free.
    """

    async def checker(
        principal: ApiKeyPrincipal = Depends(get_api_key_principal),
    ) -> ApiKeyPrincipal:
        if scope not in principal.scopes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="API key lacks the required scope.",
            )
        return principal

    return checker
