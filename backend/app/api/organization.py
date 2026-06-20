"""Organization settings endpoints."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.api.deps import ROLE_ADMIN, get_current_user, require_roles
from app.database import get_control_db
from app.models.organization import Organization
from app.models.user import User
from app.schemas.organization import (
    BrandConfig,
    CompanyProfile,
    InvoiceDefaults,
    OrganizationResponse,
    UpdateOrganizationRequest,
)
from app.services.audit_dispatch import dispatch_auth_audit
from app.services.data_residency import (
    DEFAULT_REGION,
    SUPPORTED_REGIONS,
    get_region_placement,
    resolve_region,
)
from app.services.sso import generate_scim_token
from app.tenant import get_tenant

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/organization", tags=["organization"])


class DataResidencyResponse(BaseModel):
    """Where this tenant's data is pinned to live (GDPR/CCPA residency)."""

    region: str  # the tenant's effective residency region (override → default)
    default_region: str  # platform default, so the UI can show "(default)"
    supported_regions: list[str]
    placement: dict[str, str]  # documented DB/object-storage target for `region`


class UpdateDataResidencyRequest(BaseModel):
    region: str  # must be one of SUPPORTED_REGIONS; validated server-side


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


@router.get("/fraud-rules/defaults")
async def get_fraud_rule_defaults(
    user: User = Depends(require_roles(ROLE_ADMIN)),
):
    """Return the canonical fraud-rule defaults baked into the warning
    engine. The Org Settings UI uses this as a starting point so a stale
    UI can't drift from what the engine actually evaluates."""
    from app.services.invoice_warnings import DEFAULT_FRAUD_RULES

    return DEFAULT_FRAUD_RULES


@router.patch("", response_model=OrganizationResponse)
async def update_organization(
    body: UpdateOrganizationRequest,
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(ROLE_ADMIN)),
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


@router.get("/data-residency", response_model=DataResidencyResponse)
async def get_data_residency(
    org: Organization = Depends(get_tenant),
    user: User = Depends(get_current_user),
):
    """Return the tenant's effective data-residency region + its placement.

    Read-gated to any authenticated org user (same as `GET /api/organization`);
    only the mutate path is admin-only. The placement block is the documented
    DB-cluster + object-storage target the region maps to — see
    `docs/data-residency.md` for the single-region reality + multi-region plan.
    """
    region = resolve_region(org)
    return DataResidencyResponse(
        region=region,
        default_region=DEFAULT_REGION,
        supported_regions=list(SUPPORTED_REGIONS),
        placement=get_region_placement(region),
    )


@router.put("/data-residency", response_model=DataResidencyResponse)
async def update_data_residency(
    body: UpdateDataResidencyRequest,
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(ROLE_ADMIN)),
    db: AsyncSession = Depends(get_control_db),
):
    """Pin this tenant to a residency region. Admin only; audited.

    Validates against `SUPPORTED_REGIONS` before any write (an unknown region is
    422, so a typo can't strand a tenant on a dead placement key). Writes to
    `org.settings["residency"]["region"]` via `flag_modified` (in-place nested
    JSONB mutation otherwise isn't marked dirty) and audits the change into the
    tenant trail. Changing the region is a *configuration* change — it does not
    itself migrate data; multi-region data movement is an infra operation tracked
    separately (see `docs/data-residency.md`).
    """
    if body.region not in SUPPORTED_REGIONS:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported region '{body.region}'; valid: {list(SUPPORTED_REGIONS)}",
        )

    before = resolve_region(org)

    existing = dict(org.settings or {})
    residency = dict(existing.get("residency") or {})
    residency["region"] = body.region
    existing["residency"] = residency
    org.settings = existing
    # Mutating a nested dict in-place doesn't mark JSONB dirty on its own.
    flag_modified(org, "settings")

    await db.commit()

    # Audit the config change into the TENANT trail (where every other mutation
    # for this tenant lands). Settings live on the control plane, so use the
    # self-committing tenant-audit helper. PII-free: only region tokens.
    await dispatch_auth_audit(
        organization_id=org.id,
        actor_id=user.id,
        action="organization.residency_updated",
        entity_id=org.id,
        details={"region": {"old": before, "new": body.region}},
    )

    return DataResidencyResponse(
        region=body.region,
        default_region=DEFAULT_REGION,
        supported_regions=list(SUPPORTED_REGIONS),
        placement=get_region_placement(body.region),
    )


def _resolve_brand(org: Organization) -> BrandConfig:
    """Parse `settings.brand` into a validated BrandConfig, tolerating a missing
    or malformed block by returning all-empty (= platform defaults)."""
    raw = (org.settings or {}).get("brand")
    if not isinstance(raw, dict):
        return BrandConfig()
    try:
        return BrandConfig(**raw)
    except Exception:
        # A persisted-but-now-invalid brand block must never break the read.
        return BrandConfig()


@router.get("/branding", response_model=BrandConfig)
async def get_branding(
    org: Organization = Depends(get_tenant),
    user: User = Depends(get_current_user),
):
    """Return this tenant's white-label branding config.

    Read-gated to any authenticated org user (same posture as
    `GET /api/organization` / data-residency) — the whole app needs the brand to
    theme itself, not just admins. Only the mutate path is admin-only. Empty
    fields mean "use the platform default" on the client.
    """
    return _resolve_brand(org)


@router.put("/branding", response_model=BrandConfig)
async def update_branding(
    body: BrandConfig,
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(ROLE_ADMIN)),
    db: AsyncSession = Depends(get_control_db),
):
    """Update this tenant's white-label branding. Admin only; audited.

    Pydantic has already validated the payload (hex colors, http(s) URLs), so by
    the time we get here the values are safe to persist + later inject into the
    DOM. Written to `org.settings["brand"]` via `flag_modified` (nested JSONB
    in-place mutation isn't auto-marked dirty), then audited into the tenant
    trail. PII-free: only the configured branding fields (a logo URL, links, a
    product name, colors) — never user data.
    """
    existing = dict(org.settings or {})
    existing["brand"] = body.model_dump()
    org.settings = existing
    flag_modified(org, "settings")

    await db.commit()

    await dispatch_auth_audit(
        organization_id=org.id,
        actor_id=user.id,
        action="organization.branding_updated",
        entity_id=org.id,
        details={
            # Booleans only — record *which* fields are now set, never echo a
            # raw value into the audit trail.
            "product_name_set": bool(body.product_name),
            "logo_url_set": bool(body.logo_url),
            "accent_color_set": bool(body.accent_color),
            "support_url_set": bool(body.support_url),
            "legal_url_set": bool(body.legal_url),
        },
    )

    return body


@router.post("/test-erp")
async def test_erp_connection(
    request: dict | None = None,
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(ROLE_ADMIN)),
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
    except Exception:
        logger.exception("ERP test_connection failed")
        return {"success": False, "message": "Connection failed — check your credentials"}


@router.post("/sso/scim-token", response_model=SCIMTokenResponse)
async def mint_scim_token(
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(ROLE_ADMIN)),
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
    # Mirror onto the indexed column — this is what SCIM auth resolves on
    # since migration 0021. settings.sso.scim_bearer_hash stays populated
    # for backward compat (logs, audit history) but is no longer authoritative.
    org.scim_bearer_hash = digest

    await db.commit()
    return SCIMTokenResponse(token=raw, bearer_hash_prefix=digest[:8])


@router.post("/test-payments")
async def test_payment_connection(
    request: dict | None = None,
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(ROLE_ADMIN)),
):
    """Test the payment processor connection.

    Uses request body if provided (for the "Test Connection" button before
    saving), otherwise the saved org settings.
    """
    config = (
        request if request and request.get("provider") else (org.settings or {}).get("payments")
    )
    if not config:
        raise HTTPException(status_code=400, detail="No payment processor configuration provided")

    # Trigger registration of all bundled adapters.
    from app.services.payment_adapters import get_payment_adapter

    try:
        adapter = get_payment_adapter(config)
        success = await adapter.test_connection()
        provider = config.get("provider", "unknown")
        if success:
            return {"success": True, "message": f"Connected to {provider} successfully"}
        # Surface the most likely cause without leaking key material.
        if provider == "modern_treasury":
            return {
                "success": False,
                "message": (
                    "Modern Treasury rejected the credentials — verify the "
                    "Organization ID, API key, and that the key has API access enabled."
                ),
            }
        return {"success": False, "message": "Connection failed — check your configuration"}
    except Exception:
        logger.exception("Payments test_connection failed")
        return {"success": False, "message": "Connection failed — check your configuration"}


@router.post("/test-extraction")
async def test_extraction_connection(
    request: dict | None = None,
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(ROLE_ADMIN)),
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
    except Exception:
        logger.exception("Extraction test_connection failed")
        return {"success": False, "message": "Connection failed — check your configuration"}
