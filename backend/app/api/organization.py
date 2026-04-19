"""Organization settings endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.api.deps import get_current_user
from app.database import get_control_db
from app.models.organization import Organization
from app.models.user import User
from app.schemas.organization import (
    CompanyProfile,
    InvoiceDefaults,
    OrganizationResponse,
    UpdateOrganizationRequest,
)
from app.services.sso import generate_scim_token
from app.tenant import get_tenant

router = APIRouter(prefix="/organization", tags=["organization"])


class SCIMTokenResponse(BaseModel):
    """Returned ONCE on token generation. The plaintext `token` is never
    re-served — only the sha256 of it is persisted server-side."""

    token: str
    bearer_hash_prefix: str  # first 8 hex chars, useful as a UI identifier


def _org_response(org: Organization) -> OrganizationResponse:
    raw = org.settings or {}
    # Ensure company and invoice_defaults have defaults
    if "company" not in raw:
        raw["company"] = CompanyProfile().model_dump()
    if "invoice_defaults" not in raw:
        raw["invoice_defaults"] = InvoiceDefaults().model_dump()
    return OrganizationResponse(
        id=str(org.id),
        name=org.name,
        slug=org.slug,
        plan=org.plan,
        settings=raw,
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
        # Merge incoming keys into existing settings (don't replace the whole dict)
        existing = dict(org.settings or {})
        existing.update(body.settings)
        org.settings = existing

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
    import app.services.erp_adapters.dynamics_365_bc  # noqa: F401
    import app.services.erp_adapters.merge_dev  # noqa: F401
    import app.services.erp_adapters.mock_adapter  # noqa: F401
    import app.services.erp_adapters.netsuite  # noqa: F401
    from app.services.erp_adapters import get_erp_adapter

    try:
        adapter = get_erp_adapter(erp_config)
        success = await adapter.test_connection()
        if success:
            return {
                "success": True,
                "message": f"Connected to {erp_config.get('type', 'ERP')} successfully",
            }
        else:
            return {"success": False, "message": "Connection failed — check your credentials"}
    except Exception as exc:
        return {"success": False, "message": str(exc)}


@router.post("/sso/scim-token", response_model=SCIMTokenResponse)
async def mint_scim_token(
    org: Organization = Depends(get_tenant),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_control_db),
):
    """Mint a fresh SCIM bearer token for this tenant.

    Returns the plaintext token in the response — the admin pastes it into
    Okta/Entra and we never see it again. Only the sha256 hex digest is stored
    in `org.settings.sso.scim_bearer_hash`. Rotating is a re-call: the previous
    hash is overwritten and any IdP still using the old token will start
    getting 401s, which is the desired behaviour.
    """
    raw, digest = generate_scim_token()

    settings_dict = dict(org.settings or {})
    sso = dict(settings_dict.get("sso") or {})
    sso["scim_bearer_hash"] = digest
    settings_dict["sso"] = sso
    org.settings = settings_dict
    # Mutating a nested dict in-place doesn't mark JSONB dirty on its own.
    flag_modified(org, "settings")

    await db.commit()
    return SCIMTokenResponse(token=raw, bearer_hash_prefix=digest[:8])


@router.post("/test-extraction")
async def test_extraction_connection(
    request: dict | None = None,
    org: Organization = Depends(get_tenant),
    user: User = Depends(get_current_user),
):
    """Test the AI extraction provider connection. Uses request body config if provided."""
    config = (
        request if request and request.get("provider") else (org.settings or {}).get("extraction")
    )
    if not config:
        raise HTTPException(status_code=400, detail="No extraction configuration provided")

    import app.services.extraction_adapters.aws_textract  # noqa: F401
    import app.services.extraction_adapters.claude_vision  # noqa: F401
    import app.services.extraction_adapters.mock_adapter  # noqa: F401
    import app.services.extraction_adapters.ollama  # noqa: F401
    import app.services.extraction_adapters.openai_vision  # noqa: F401
    from app.services.extraction_adapters import get_extraction_adapter

    try:
        adapter = get_extraction_adapter(config)
        success = await adapter.test_connection()
        provider = config.get("provider", "unknown")
        if success:
            return {"success": True, "message": f"Connected to {provider} successfully"}
        else:
            if provider == "ollama":
                model = config.get("model", "llama3.2-vision:11b")
                return {
                    "success": False,
                    "message": (
                        f"Ollama is running but model '{model}' not found. Run: ollama pull {model}"
                    ),
                }
            return {"success": False, "message": "Connection failed — check your configuration"}
    except Exception as exc:
        return {"success": False, "message": str(exc)}
