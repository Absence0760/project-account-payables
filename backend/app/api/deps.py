"""Shared FastAPI dependencies for auth, tenant context, and DB sessions."""

import logging
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import Depends, Header, HTTPException, Request, status
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_control_db
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
    jti = str(uuid.uuid4())
    expire = datetime.now(UTC) + timedelta(minutes=settings.access_token_expire_minutes)
    payload = {
        "sub": str(user_id),
        "org": str(org_id),
        "typ": "user",
        "jti": jti,
        "exp": expire,
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


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
