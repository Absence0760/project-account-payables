"""SCIM 2.0 /Users endpoints for Okta + Entra provisioning.

Each tenant has its own SCIM base URL:
    https://app.com/api/scim/v2/Users

and its own bearer token (hash stored in Organization.settings.sso.
scim_bearer_hash). The IdP is configured once per tenant with that URL +
token and pushes users here: list, get by id, create, PATCH (SCIM's
partial-update flavor), delete (soft — sets active=false).

What's NOT here: /Groups (requires RBAC-group → Role mapping design work).
Planned for a follow-up PR.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_control_db
from app.models.organization import Organization
from app.models.user import User
from app.schemas.scim import (
    USER_SCHEMA,
    SCIMEmail,
    SCIMError,
    SCIMListResponse,
    SCIMMeta,
    SCIMName,
    SCIMPatchRequest,
    SCIMUser,
    SCIMUserCreate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/scim/v2", tags=["scim"])


# ---------------------------------------------------------------------------
# Auth: tenant is resolved from the bearer token's hash.
# ---------------------------------------------------------------------------


async def get_scim_tenant(
    authorization: Annotated[str | None, Header()] = None,
    db: AsyncSession = Depends(get_control_db),
) -> Organization:
    """Resolve the tenant for this SCIM request from the Authorization header."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise _scim_http_error(401, "Missing or malformed Authorization header.")

    token = authorization.split(None, 1)[1].strip()
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()

    # Indexed lookup via the dedicated column (migration 0021). The
    # settings.sso.scim_bearer_hash key is still mirrored for backward
    # compatibility but is no longer the source of truth for auth.
    result = await db.execute(select(Organization).where(Organization.scim_bearer_hash == digest))
    org = result.scalar_one_or_none()
    if org is None:
        raise _scim_http_error(401, "Bearer token not recognised.")
    return org


def _scim_http_error(status: int, detail: str, scim_type: str | None = None) -> HTTPException:
    """Build a SCIM-compliant error response. IdPs rely on this shape."""
    body = SCIMError(status=str(status), detail=detail, scimType=scim_type).model_dump()
    return HTTPException(status_code=status, detail=body)


# ---------------------------------------------------------------------------
# Domain mapping: User row <-> SCIM representation.
# ---------------------------------------------------------------------------


def _user_to_scim(user: User, request: Request) -> SCIMUser:
    base = f"{str(request.base_url).rstrip('/')}{settings.scim_url_path}/Users/{user.id}"
    emails = [SCIMEmail(value=user.email, primary=True, type="work")]

    # Best-effort split of full_name → given/family. Whatever the IdP
    # pushed in originally wins on PATCH; this is just for GET responses.
    given, family = "", ""
    if user.full_name:
        parts = user.full_name.split(None, 1)
        given = parts[0]
        family = parts[1] if len(parts) > 1 else ""

    return SCIMUser(
        id=str(user.id),
        userName=user.email,
        externalId=user.sso_provider_id,
        name=SCIMName(givenName=given, familyName=family, formatted=user.full_name),
        emails=emails,
        active=user.is_active,
        meta=SCIMMeta(
            resourceType="User",
            created=user.created_at.isoformat() if user.created_at else None,
            lastModified=user.updated_at.isoformat() if user.updated_at else None,
            location=base,
        ),
    )


def _extract_primary_email(emails: list[SCIMEmail], fallback: str) -> str:
    for e in emails:
        if e.primary and e.value:
            return e.value
    for e in emails:
        if e.value:
            return e.value
    return fallback


def _extract_full_name(name: SCIMName | None, email: str) -> str:
    if name is None:
        return email.split("@", 1)[0]
    if name.formatted:
        return name.formatted
    parts = [name.givenName or "", name.familyName or ""]
    joined = " ".join(p for p in parts if p).strip()
    return joined or email.split("@", 1)[0]


# ---------------------------------------------------------------------------
# Filter parser — SCIM filters are their own mini-language; we support the
# subset Okta + Entra actually use for user provisioning. Anything else
# returns a 400 so the IdP shows a clear error to the admin.
# ---------------------------------------------------------------------------


def _apply_filter(query, filter_expr: str):
    """Apply a SCIM filter to a SQLAlchemy query. Supports:
        userName eq "email"
        emails eq "email"        (same — we store one email per user)
        externalId eq "xyz"
        active eq true / false
    Unsupported operators raise a SCIM-flavoured 400.
    """
    expr = filter_expr.strip()
    for attr, column in [
        ("userName", User.email),
        ("emails", User.email),
        ("externalId", User.sso_provider_id),
    ]:
        prefix = f"{attr} eq "
        if expr.startswith(prefix):
            value = expr[len(prefix) :].strip().strip('"')
            return query.where(column == value)

    if expr in ("active eq true", "active eq True"):
        return query.where(User.is_active.is_(True))
    if expr in ("active eq false", "active eq False"):
        return query.where(User.is_active.is_(False))

    raise _scim_http_error(
        400,
        f"Unsupported filter: {filter_expr}. Supported: userName/emails/externalId eq, active eq.",
        scim_type="invalidFilter",
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/Users", response_model=SCIMListResponse)
async def list_users(
    request: Request,
    startIndex: int = Query(1, ge=1),
    count: int = Query(100, ge=0, le=1000),
    filter: str | None = Query(None),
    org: Organization = Depends(get_scim_tenant),
    db: AsyncSession = Depends(get_control_db),
):
    base_query = select(User).where(User.organization_id == org.id)
    if filter:
        base_query = _apply_filter(base_query, filter)

    total = (
        await db.execute(select(func.count()).select_from(base_query.subquery()))
    ).scalar() or 0

    # SCIM uses 1-based indexing
    page_query = base_query.order_by(User.created_at).offset(startIndex - 1).limit(count)
    result = await db.execute(page_query)
    users = result.scalars().all()

    return SCIMListResponse(
        totalResults=total,
        startIndex=startIndex,
        itemsPerPage=len(users),
        Resources=[_user_to_scim(u, request) for u in users],
    )


@router.get("/Users/{user_id}", response_model=SCIMUser)
async def get_user(
    user_id: uuid.UUID,
    request: Request,
    org: Organization = Depends(get_scim_tenant),
    db: AsyncSession = Depends(get_control_db),
):
    result = await db.execute(
        select(User).where(User.id == user_id, User.organization_id == org.id)
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise _scim_http_error(404, f"User {user_id} not found.")
    return _user_to_scim(user, request)


@router.post("/Users", response_model=SCIMUser, status_code=201)
async def create_user(
    body: SCIMUserCreate,
    request: Request,
    org: Organization = Depends(get_scim_tenant),
    db: AsyncSession = Depends(get_control_db),
):
    email = _extract_primary_email(body.emails, body.userName).lower().strip()

    # SCIM requires 409 on duplicate userName.
    existing = (
        await db.execute(select(User).where(User.email == email, User.organization_id == org.id))
    ).scalar_one_or_none()
    if existing is not None:
        raise _scim_http_error(409, f"User with userName {email} already exists.", "uniqueness")

    sso = (org.settings or {}).get("sso") or {}
    provider = sso.get("provider") or "oidc"

    user = User(
        id=uuid.uuid4(),
        email=email,
        full_name=_extract_full_name(body.name, email),
        sso_provider=provider,
        sso_provider_id=body.externalId,
        hashed_password=None,  # SCIM-managed users are SSO-only
        is_active=body.active,
        organization_id=org.id,
        must_change_password=False,
    )
    db.add(user)
    await db.flush()
    logger.info("SCIM provisioned user %s in org %s", email, org.slug)
    return _user_to_scim(user, request)


@router.patch("/Users/{user_id}", response_model=SCIMUser)
async def patch_user(
    user_id: uuid.UUID,
    body: SCIMPatchRequest,
    request: Request,
    org: Organization = Depends(get_scim_tenant),
    db: AsyncSession = Depends(get_control_db),
):
    """Apply a subset of PATCH operations. We support the ops Okta + Entra
    send for user lifecycle: setting active=false on deprovision, replacing
    name/email, updating externalId."""
    result = await db.execute(
        select(User).where(User.id == user_id, User.organization_id == org.id)
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise _scim_http_error(404, f"User {user_id} not found.")

    for op in body.Operations:
        action = (op.op or "").lower()
        path = (op.path or "").strip()
        value = op.value

        if action == "remove" and path == "":
            # Ignore — empty remove is a no-op
            continue

        if path.lower() == "active":
            user.is_active = bool(value) if action != "remove" else False
        elif path == "userName" and action in ("replace", "add"):
            if isinstance(value, str):
                user.email = value.lower().strip()
        elif path == "externalId" and action in ("replace", "add"):
            if isinstance(value, str):
                user.sso_provider_id = value
        elif path == "" and isinstance(value, dict) and action == "replace":
            # Bulk replace on the root — common from Okta for status changes
            if "active" in value:
                user.is_active = bool(value["active"])
            if "userName" in value and isinstance(value["userName"], str):
                user.email = value["userName"].lower().strip()
            if "externalId" in value and isinstance(value["externalId"], str):
                user.sso_provider_id = value["externalId"]
            if "name" in value and isinstance(value["name"], dict):
                name = value["name"]
                user.full_name = _extract_full_name(SCIMName(**name), user.email)
        else:
            # Silently ignore unsupported ops — SCIM lets the server do this,
            # and IdPs sometimes push ops we don't model (phoneNumbers etc.)
            logger.debug("Ignoring unsupported SCIM PATCH op: %s %s", action, path)

    await db.flush()
    return _user_to_scim(user, request)


@router.delete("/Users/{user_id}", status_code=204)
async def delete_user(
    user_id: uuid.UUID,
    org: Organization = Depends(get_scim_tenant),
    db: AsyncSession = Depends(get_control_db),
):
    """Soft delete — SCIM DELETE deactivates rather than hard-deletes, so
    we preserve the audit trail."""
    result = await db.execute(
        select(User).where(User.id == user_id, User.organization_id == org.id)
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise _scim_http_error(404, f"User {user_id} not found.")
    user.is_active = False
    await db.flush()


@router.get("/ServiceProviderConfig")
async def service_provider_config():
    """Minimal discovery doc that Okta/Entra probe to learn what we support."""
    return {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
        "patch": {"supported": True},
        "bulk": {"supported": False},
        "filter": {"supported": True, "maxResults": 200},
        "changePassword": {"supported": False},
        "sort": {"supported": False},
        "etag": {"supported": False},
        "authenticationSchemes": [
            {
                "type": "oauthbearertoken",
                "name": "OAuth Bearer Token",
                "description": "Per-tenant bearer token configured in Organization settings.",
            }
        ],
    }


@router.get("/Schemas/{schema_id}")
async def schemas(schema_id: str):
    """Entra probes this. Return the core User schema — enough for both IdPs
    to stop complaining and proceed with user sync."""
    if schema_id != USER_SCHEMA:
        raise _scim_http_error(404, f"Schema {schema_id} not known.")
    return {
        "id": USER_SCHEMA,
        "name": "User",
        "description": "User Account",
        "attributes": [
            {"name": "userName", "type": "string", "required": True, "uniqueness": "server"},
            {"name": "externalId", "type": "string"},
            {"name": "active", "type": "boolean"},
            {"name": "name", "type": "complex"},
            {"name": "emails", "type": "complex", "multiValued": True},
        ],
    }
