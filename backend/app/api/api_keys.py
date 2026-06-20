"""API-key management — mint / list / revoke programmatic keys.

These are the *admin-facing* endpoints, gated by the normal JWT session +
``require_roles(ROLE_ADMIN)`` (NOT the X-API-Key path — an admin can't be
expected to hold a key to make the first one). They operate on the
control-plane ``api_keys`` table. Mint returns the plaintext key EXACTLY ONCE;
list returns prefix + metadata only — never the hash or plaintext. Every
mint / revoke writes an append-only, PII-free audit row.
"""

import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ROLE_ADMIN, require_roles
from app.database import get_control_db
from app.models.api_key import ApiKey
from app.models.organization import Organization
from app.models.user import User
from app.services.api_keys import generate_api_key
from app.services.audit_dispatch import dispatch_auth_audit
from app.tenant import get_tenant

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api-keys", tags=["api-keys"])


class CreateApiKeyRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)


class ApiKeyResponse(BaseModel):
    """Metadata for an API key — never carries the hash or plaintext."""

    model_config = {"from_attributes": True}

    id: uuid.UUID
    name: str
    key_prefix: str
    scopes: list[str]
    created_at: datetime | None = None
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None


class ApiKeyCreatedResponse(BaseModel):
    """The mint response — the ONLY place the plaintext key is ever returned."""

    api_key: ApiKeyResponse
    # Shown once; the admin must copy it now. Never stored or logged.
    key: str


@router.post("", response_model=ApiKeyCreatedResponse, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    body: CreateApiKeyRequest,
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(ROLE_ADMIN)),
    db: AsyncSession = Depends(get_control_db),
) -> ApiKeyCreatedResponse:
    """Mint a new read-scoped API key for this org. Returns the plaintext once."""
    full_key, prefix, key_hash = generate_api_key()
    row = ApiKey(
        id=uuid.uuid4(),
        organization_id=org.id,
        name=body.name,
        key_prefix=prefix,
        key_hash=key_hash,
        scopes=["read"],
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    # Append-only audit — PII-free: records the prefix (non-secret) + scopes,
    # never the plaintext or hash.
    await dispatch_auth_audit(
        organization_id=org.id,
        actor_id=user.id,
        action="api_key.created",
        entity_id=row.id,
        details={"key_prefix": row.key_prefix, "name": row.name, "scopes": row.scopes},
    )

    return ApiKeyCreatedResponse(api_key=ApiKeyResponse.model_validate(row), key=full_key)


@router.get("", response_model=list[ApiKeyResponse])
async def list_api_keys(
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(ROLE_ADMIN)),
    db: AsyncSession = Depends(get_control_db),
) -> list[ApiKeyResponse]:
    """List this org's API keys (active + revoked). Metadata only."""
    rows = (
        (
            await db.execute(
                select(ApiKey)
                .where(ApiKey.organization_id == org.id)
                .order_by(ApiKey.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [ApiKeyResponse.model_validate(r) for r in rows]


@router.delete("/{key_id}", response_model=ApiKeyResponse)
async def revoke_api_key(
    key_id: uuid.UUID,
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(ROLE_ADMIN)),
    db: AsyncSession = Depends(get_control_db),
) -> ApiKeyResponse:
    """Revoke a key (soft — the row stays for audit). Idempotent."""
    row = (
        await db.execute(
            select(ApiKey).where(ApiKey.id == key_id, ApiKey.organization_id == org.id)
        )
    ).scalar_one_or_none()
    if row is None:
        # Same 404 for wrong-org and missing so the response can't enumerate
        # another tenant's key ids.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found")

    if row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(row)
        await dispatch_auth_audit(
            organization_id=org.id,
            actor_id=user.id,
            action="api_key.revoked",
            entity_id=row.id,
            details={"key_prefix": row.key_prefix, "name": row.name},
        )

    return ApiKeyResponse.model_validate(row)
