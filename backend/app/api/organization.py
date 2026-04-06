"""Organization settings endpoints."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.database import get_control_db
from app.models.organization import Organization
from app.models.user import User
from app.tenant import get_tenant
from app.schemas.organization import (
    OrganizationResponse,
    OrganizationSettings,
    UpdateOrganizationRequest,
)

router = APIRouter(prefix="/organization", tags=["organization"])


def _org_response(org: Organization) -> OrganizationResponse:
    raw = org.settings or {}
    settings = OrganizationSettings(**raw) if raw else OrganizationSettings()
    return OrganizationResponse(
        id=str(org.id),
        name=org.name,
        slug=org.slug,
        plan=org.plan,
        settings=settings,
        created_at=org.created_at.isoformat() if org.created_at else "",
    )


@router.get("", response_model=OrganizationResponse)
async def get_organization(
    org: Organization = Depends(get_tenant),
    user: User = Depends(get_current_user),
):
    return _org_response(org)


@router.patch("", response_model=OrganizationResponse)
async def update_organization(
    body: UpdateOrganizationRequest,
    org: Organization = Depends(get_tenant),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_control_db),
):
    if body.name is not None:
        org.name = body.name

    if body.settings is not None:
        org.settings = body.settings.model_dump()

    await db.commit()
    return _org_response(org)
