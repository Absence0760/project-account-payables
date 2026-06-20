"""Partner / reseller multi-tenant admin (white-label).

A *partner* (reseller) org administers a set of branded *child* tenants. The
relationship is the nullable self-FK ``Organization.parent_org_id`` (migration
0065, control plane): a child points at its parent; a partner is any org
referenced by >= 1 child.

Trust model (the load-bearing point): the caller's org is resolved by the
standard ``get_tenant`` chokepoint, which cross-checks the JWT ``org`` claim
against the resolved tenant — so a partner admin authenticates as, and can only
act as, *their own* partner org. EVERY child query in this module is then
scoped ``Organization.parent_org_id == partner.id``: a partner can never see or
mutate an org it didn't parent. Looking up a non-child org id yields the same
opaque 404 as a missing one (no cross-tenant enumeration).

Each child's branding write lands on the CHILD org's control-plane row
(``settings.brand`` JSONB) and is audited into the CHILD's tenant trail —
exactly the same write + audit path as the child's own
``PUT /api/organization/branding``, just initiated by the parent. PII-free
audit (booleans only, never raw values). Admin-only on every mutation.
"""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.api.deps import ROLE_ADMIN, require_roles
from app.api.organization import _resolve_brand
from app.database import get_control_db
from app.models.organization import Organization
from app.models.user import User
from app.schemas.organization import BrandConfig
from app.services.audit_dispatch import dispatch_auth_audit
from app.tenant import get_tenant

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/partner", tags=["partner"])


class ChildTenantSummary(BaseModel):
    """A single child tenant, as seen by its administering partner."""

    id: uuid.UUID
    name: str
    slug: str
    plan: str
    product_name: str  # the child's resolved white-label product name ("" = default)


class PartnerOverview(BaseModel):
    """The partner's own identity + its child tenants. ``is_partner`` is True
    when the org administers >= 1 child (no separate flag — it's derived)."""

    organization_id: uuid.UUID
    name: str
    is_partner: bool
    children: list[ChildTenantSummary]


async def _resolve_child(
    db: AsyncSession, *, partner_id: uuid.UUID, child_id: uuid.UUID
) -> Organization:
    """Load a child org, enforcing that it is parented by ``partner_id``.

    Data-layer scoping: the WHERE clause requires BOTH the id match AND
    ``parent_org_id == partner_id``, so a partner can only ever reach its own
    children. A non-child (or unknown) id is the SAME opaque 404 so the response
    can't be used to enumerate other tenants' org ids.
    """
    child = (
        await db.execute(
            select(Organization).where(
                Organization.id == child_id,
                Organization.parent_org_id == partner_id,
            )
        )
    ).scalar_one_or_none()
    if child is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Child tenant not found")
    return child


def _child_summary(child: Organization) -> ChildTenantSummary:
    return ChildTenantSummary(
        id=child.id,
        name=child.name,
        slug=child.slug,
        plan=child.plan,
        product_name=_resolve_brand(child).product_name,
    )


@router.get("", response_model=PartnerOverview)
async def get_partner_overview(
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(ROLE_ADMIN)),
    db: AsyncSession = Depends(get_control_db),
) -> PartnerOverview:
    """List the child tenants this partner org administers.

    Admin-only. Scoped to ``parent_org_id == org.id`` — never returns a tenant
    the caller doesn't parent. A standalone org (no children) gets an empty list
    and ``is_partner: false``, so the UI can render an empty/"not a partner"
    state without a distinct error.
    """
    children = (
        (
            await db.execute(
                select(Organization)
                .where(Organization.parent_org_id == org.id)
                .order_by(Organization.name)
            )
        )
        .scalars()
        .all()
    )
    return PartnerOverview(
        organization_id=org.id,
        name=org.name,
        is_partner=len(children) > 0,
        children=[_child_summary(c) for c in children],
    )


@router.get("/children/{child_id}/branding", response_model=BrandConfig)
async def get_child_branding(
    child_id: uuid.UUID,
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(ROLE_ADMIN)),
    db: AsyncSession = Depends(get_control_db),
) -> BrandConfig:
    """Read one child tenant's white-label branding.

    Admin-only; the child must be parented by the caller (else opaque 404). The
    resolver tolerates a missing / malformed brand block by returning all-empty
    (= platform defaults), identical to the child's own GET.
    """
    child = await _resolve_child(db, partner_id=org.id, child_id=child_id)
    return _resolve_brand(child)


@router.put("/children/{child_id}/branding", response_model=BrandConfig)
async def update_child_branding(
    child_id: uuid.UUID,
    body: BrandConfig,
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(ROLE_ADMIN)),
    db: AsyncSession = Depends(get_control_db),
) -> BrandConfig:
    """Push branding onto one child tenant. Admin-only; audited into the CHILD's trail.

    The child must be parented by the caller (else opaque 404). Pydantic has
    already validated the payload (hex colors, http(s) URLs). The write lands on
    the CHILD org's ``settings.brand`` (control plane) and PRESERVES
    ``custom_domains`` — the same carry-forward the child's own
    ``PUT /api/organization/branding`` does, so a partner-initiated brand save
    can't silently wipe the child's registered vanity hostnames. The audit row
    is written into the CHILD's tenant trail (``organization.branding_updated``,
    with ``via: "partner"`` + the acting partner org id so the change is
    attributable) — PII-free, booleans only.
    """
    child = await _resolve_child(db, partner_id=org.id, child_id=child_id)

    existing = dict(child.settings or {})
    prior_brand = existing.get("brand")
    new_brand = body.model_dump()
    if isinstance(prior_brand, dict) and "custom_domains" in prior_brand:
        new_brand["custom_domains"] = prior_brand["custom_domains"]
    existing["brand"] = new_brand
    child.settings = existing
    # Mutating a nested dict in-place doesn't mark JSONB dirty on its own.
    flag_modified(child, "settings")

    await db.commit()

    # Audit into the CHILD's tenant trail (where every other mutation for that
    # tenant lands), not the partner's — the branding belongs to the child.
    # PII-free: which fields are now set (booleans), plus the partner attribution.
    await dispatch_auth_audit(
        organization_id=child.id,
        actor_id=user.id,
        action="organization.branding_updated",
        entity_type="organization",
        entity_id=child.id,
        details={
            "via": "partner",
            "partner_org_id": str(org.id),
            "product_name_set": bool(body.product_name),
            "logo_url_set": bool(body.logo_url),
            "accent_color_set": bool(body.accent_color),
            "support_url_set": bool(body.support_url),
            "legal_url_set": bool(body.legal_url),
        },
    )

    return body
