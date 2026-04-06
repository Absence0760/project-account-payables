"""Organization settings endpoints."""

from fastapi import APIRouter, Depends, HTTPException
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


@router.post("/test-erp")
async def test_erp_connection(
    request: dict | None = None,
    org: Organization = Depends(get_tenant),
    user: User = Depends(get_current_user),
):
    """Test the ERP connection. Uses request body config if provided, otherwise saved config."""
    erp_config = request if request and request.get("type") else (org.settings or {}).get("erp")
    if not erp_config:
        raise HTTPException(status_code=400, detail="No ERP configuration provided")

    # Import adapters to trigger registration
    import app.services.erp_adapters.mock_adapter  # noqa: F401
    import app.services.erp_adapters.merge_dev  # noqa: F401
    import app.services.erp_adapters.dynamics_365_bc  # noqa: F401
    import app.services.erp_adapters.netsuite  # noqa: F401

    from app.services.erp_adapters import get_erp_adapter

    try:
        adapter = get_erp_adapter(erp_config)
        success = await adapter.test_connection()
        if success:
            return {"success": True, "message": f"Connected to {erp_config.get('type', 'ERP')} successfully"}
        else:
            return {"success": False, "message": "Connection failed — check your credentials"}
    except Exception as exc:
        return {"success": False, "message": str(exc)}
