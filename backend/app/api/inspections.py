"""Quality Inspection endpoints — list, detail, create (4-way matching leg).

A QualityInspection is the 4th leg of 4-way matching (invoice vs PO vs Goods
Receipt vs Quality Inspection). ``po_matching.match_invoice_to_po`` reads the
most recent inspection for a PO/GR to drive pass/fail/partial outcomes; this
router is the CRUD surface that creates those rows. See
``backend/docs/po-matching.md``.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    ROLE_ADMIN,
    ROLE_AP_MANAGER,
    get_current_user,
    get_org_id,
    require_roles,
)
from app.models.quality_inspection import QualityInspection
from app.models.user import User
from app.schemas.inspection import VALID_RESULTS, InspectionCreate
from app.tenant import (
    apply_entity_scope,
    get_entity_id,
    get_tenant_db,
    get_write_entity_id,
)

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
