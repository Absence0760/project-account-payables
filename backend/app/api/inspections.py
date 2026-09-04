"""Quality Inspection endpoints — list, detail, create (4-way matching leg).

A QualityInspection is the 4th leg of 4-way matching (invoice vs PO vs Goods
Receipt vs Quality Inspection). ``po_matching.match_invoice_to_po`` reads the
most recent inspection for a PO/GR to drive pass/fail/partial outcomes; this
router is the CRUD surface that creates those rows. See
``backend/docs/po-matching.md``.
"""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    ROLE_ADMIN,
    ROLE_AP_MANAGER,
    get_current_user,
    get_org_id,
    require_roles,
)
from app.database import get_control_db
from app.models.organization import Organization
from app.models.quality_inspection import QualityInspection
from app.models.user import User
from app.schemas.inspection import VALID_RESULTS, InspectionCreate
from app.services.qms_adapters import UnknownQmsProviderError, list_available_providers
from app.services.qms_sync import resolve_opted_in_qms_config, sync_tenant_inspections
from app.tenant import (
    apply_entity_scope,
    get_entity_id,
    get_tenant_db,
    get_write_entity_id,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/inspections", tags=["inspections"])


def _serialize(qi: QualityInspection) -> dict:
    return {
        "id": str(qi.id),
        "inspection_number": qi.inspection_number,
        "po_id": str(qi.po_id) if qi.po_id else None,
        "gr_id": str(qi.gr_id) if qi.gr_id else None,
        "result": qi.result,
        "inspected_date": qi.inspected_date.isoformat() if qi.inspected_date else None,
        "inspector": qi.inspector,
        "accepted_quantity": (
            float(qi.accepted_quantity) if qi.accepted_quantity is not None else None
        ),
        "rejected_quantity": (
            float(qi.rejected_quantity) if qi.rejected_quantity is not None else None
        ),
        "deviation_notes": qi.deviation_notes,
        "status": qi.status,
        "created_at": qi.created_at.isoformat() if qi.created_at else "",
    }


@router.get("")
async def list_inspections(
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(get_current_user),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    query = apply_entity_scope(select(QualityInspection), QualityInspection, entity_id).order_by(
        QualityInspection.created_at.desc()
    )
    result = await db.execute(query)
    return [_serialize(qi) for qi in result.scalars().all()]


@router.post("", status_code=201)
async def create_inspection(
    body: InspectionCreate,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER)),
    org_id: uuid.UUID = Depends(get_org_id),
    entity_id: uuid.UUID = Depends(get_write_entity_id),
):
    if body.result not in VALID_RESULTS:
        raise HTTPException(
            status_code=400,
            detail=f"result must be one of {sorted(VALID_RESULTS)}",
        )

    po_id: uuid.UUID | None = None
    if body.po_id:
        try:
            po_id = uuid.UUID(body.po_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid po_id")

    gr_id: uuid.UUID | None = None
    if body.gr_id:
        try:
            gr_id = uuid.UUID(body.gr_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid gr_id")

    inspection = QualityInspection(
        inspection_number=body.inspection_number,
        po_id=po_id,
        gr_id=gr_id,
        result=body.result,
        inspected_date=body.inspected_date,
        inspector=body.inspector,
        accepted_quantity=body.accepted_quantity,
        rejected_quantity=body.rejected_quantity,
        deviation_notes=body.deviation_notes,
        organization_id=org_id,
        entity_id=entity_id,
    )
    db.add(inspection)
    await db.flush()
    return _serialize(inspection)


@router.post("/sync")
async def sync_inspections(
    db: AsyncSession = Depends(get_tenant_db),
    control_db: AsyncSession = Depends(get_control_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER)),
    org_id: uuid.UUID = Depends(get_org_id),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    """Pull quality inspections from the org's configured QMS into
    ``quality_inspections``. Idempotent (upsert keyed on
    ``(organization_id, inspection_number)``). Reads the QMS config from
    ``Organization.settings.qms`` on the control plane. Returns
    ``{fetched, created, updated, unchanged, skipped}``.

    **A full re-pull, deliberately.** The background sweep is incremental — it
    passes each org's persisted ``settings.qms.last_synced_at`` high-water mark
    and advances it. This route passes no cursor and advances none: a human
    asking to sync now is asking for everything, usually *because* they suspect
    the incremental window missed something, and answering that with an empty
    result would be useless. Re-fetched records that match what is stored come
    back as ``unchanged`` and write neither a row nor an audit entry.

    **409 when the configured provider has no adapter**, too — the other half of
    the same guard. `get_qms_adapter` refuses a NAMED provider it has no adapter
    for rather than resolving to `mock` (`decisions.md` §29), and this route
    surfaces that as a 409 naming the bad value and the registered alternatives.
    An operator asked for this pull directly; answering with a clean all-zero
    summary would hide the reason it found nothing.

    **409 when the org has no QMS configured.** Opting in is the same rule the
    background sweep applies (`qms_sync.resolve_opted_in_qms_config`, shared so
    the two can't drift): an org-level `settings.qms` block, or a platform
    provider override. Without one, `get_qms_adapter(None)` resolves to the
    `mock` adapter, and a single call would persist its three fabricated
    fixtures against the tenant's real purchase orders — a synthetic `pass`
    clearing the 4-way quality gate on a real invoice, a synthetic `fail`
    flipping others to `mismatch`, both indistinguishable from real rows.
    """
    settings_blob = (
        await control_db.execute(select(Organization.settings).where(Organization.id == org_id))
    ).scalar_one_or_none()
    qms_config = resolve_opted_in_qms_config(settings_blob)
    if qms_config is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "No QMS is configured for this organization. Set "
                "settings.qms.provider before syncing inspections."
            ),
        )

    try:
        return await sync_tenant_inspections(
            db,
            org_id=org_id,
            qms_config=qms_config,
            entity_id=entity_id,
            actor_id=user.id,
        )
    except UnknownQmsProviderError as exc:
        # The org opted in with a provider we have no adapter for. An operator
        # asked for this pull directly, so say why it did not happen rather
        # than returning a clean-looking all-zero summary (`decisions.md` §29).
        # 409, matching the sibling "no QMS configured" refusal above: the
        # request is well-formed; the org's QMS configuration is in a state that
        # cannot service it. The adapter resolves before any query, so nothing
        # is persisted — no inspection row, no audit row.
        logger.warning(
            "[inspections] QMS provider %r has no registered adapter for org=%s",
            exc.provider,
            org_id,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"'{exc.provider}' is not a supported QMS provider "
                f"(one of: {', '.join(list_available_providers())}). "
                "Fix settings.qms.provider and retry."
            ),
        ) from None


@router.get("/{inspection_id}")
async def get_inspection(
    inspection_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(QualityInspection).where(QualityInspection.id == inspection_id)
    )
    qi = result.scalar_one_or_none()
    if not qi:
        raise HTTPException(status_code=404, detail="Inspection not found")
    return _serialize(qi)
