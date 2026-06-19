"""Recurring / subscription invoice template endpoints.

CRUD + lifecycle (pause / resume / end) for
:class:`~app.models.recurring_invoice.RecurringInvoiceTemplate`, plus the
manual ``generate-now`` (idempotent), a projected upcoming schedule, and the
generated-invoice history.

All money is ``Decimal`` end-to-end; every mutation is RBAC-gated and writes an
audit row; reads are entity-scoped (multi-entity). The scheduling /
generation / variance math is shared verbatim with the background sweep via
``app.services.recurring_invoices``. See ``backend/docs/recurring-invoices.md``.
"""

from __future__ import annotations

import json
import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    ROLE_ADMIN,
    ROLE_AP_CLERK,
    ROLE_AP_MANAGER,
    ROLE_CFO,
    get_org_id,
    require_roles,
)
from app.models.invoice import Invoice
from app.models.recurring_invoice import (
    STATUS_ACTIVE,
    STATUS_ENDED,
    STATUS_PAUSED,
    RecurringInvoiceTemplate,
)
from app.models.user import User
from app.models.vendor import Vendor
from app.schemas.recurring_invoice import (
    GeneratedHistoryResponse,
    GeneratedInvoiceItem,
    RecurringTemplateCreate,
    RecurringTemplateListResponse,
    RecurringTemplateResponse,
    RecurringTemplateUpdate,
    ScheduleOccurrence,
    UpcomingScheduleResponse,
)
from app.services import recurring_invoices as svc
from app.services.audit_dispatch import dispatch_audit
from app.tenant import (
    apply_entity_scope,
    get_entity_id,
    get_tenant_db,
    get_write_entity_id,
)

router = APIRouter(prefix="/recurring", tags=["recurring"])

_READ_ROLES = (ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_AP_CLERK, ROLE_CFO)
_WRITE_ROLES = (ROLE_ADMIN, ROLE_AP_MANAGER)

# Fields whose change re-anchors `next_run_on` for an active template.
_SCHEDULE_FIELDS = {"cadence", "day_of_period", "start_date"}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


async def _resolve_vendor_name(db: AsyncSession, vendor_id: uuid.UUID | None) -> str | None:
    if vendor_id is None:
        return None
    return (
        await db.execute(select(Vendor.name).where(Vendor.id == vendor_id))
    ).scalar_one_or_none()


async def _get_scoped(
    db: AsyncSession, template_id: uuid.UUID, entity_id: uuid.UUID | None
) -> RecurringInvoiceTemplate:
    q = apply_entity_scope(
        select(RecurringInvoiceTemplate).where(RecurringInvoiceTemplate.id == template_id),
        RecurringInvoiceTemplate,
        entity_id,
    )
    template = (await db.execute(q)).scalar_one_or_none()
    if template is None:
        raise HTTPException(status_code=404, detail="Recurring template not found")
    return template


def _seed_next_run_on(template: RecurringInvoiceTemplate, *, after: date) -> None:
    """(Re)compute and stamp ``next_run_on`` from the template's own fields."""
    template.next_run_on = svc.compute_next_run_on(
        template.cadence,
        template.day_of_period,
        after=after,
        start_date=template.start_date,
        end_date=template.end_date,
    )


# --------------------------------------------------------------------------- #
# List / create / detail
# --------------------------------------------------------------------------- #


@router.get("", response_model=RecurringTemplateListResponse)
async def list_templates(
    status_filter: str | None = Query(None, alias="status"),
    vendor_id: uuid.UUID | None = None,
    search: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(*_READ_ROLES)),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    query = apply_entity_scope(
        select(RecurringInvoiceTemplate), RecurringInvoiceTemplate, entity_id
    )
    if status_filter:
        query = query.where(RecurringInvoiceTemplate.status == status_filter)
    if vendor_id:
        query = query.where(RecurringInvoiceTemplate.vendor_id == vendor_id)
    if search:
        like = f"%{search.strip()}%"
        query = query.where(
            or_(
                RecurringInvoiceTemplate.name.ilike(like),
                RecurringInvoiceTemplate.vendor_name.ilike(like),
            )
        )

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar() or 0
    query = (
        query.order_by(RecurringInvoiceTemplate.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = list((await db.execute(query)).scalars().all())
    return RecurringTemplateListResponse(
        items=[RecurringTemplateResponse.from_db(t) for t in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=RecurringTemplateResponse, status_code=status.HTTP_201_CREATED)
async def create_template(
    body: RecurringTemplateCreate,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(*_WRITE_ROLES)),
    org_id: uuid.UUID = Depends(get_org_id),
    entity_id: uuid.UUID = Depends(get_write_entity_id),
):
    vendor_uuid = uuid.UUID(body.vendor_id) if body.vendor_id else None
    vendor_name = await _resolve_vendor_name(db, vendor_uuid)

    template = RecurringInvoiceTemplate(
        organization_id=org_id,
        entity_id=entity_id,
        name=body.name,
        vendor_id=vendor_uuid,
        vendor_name=vendor_name,
        description=body.description,
        amount=body.amount,
        currency=(body.currency or "USD").upper(),
        gl_account=body.gl_account,
        cost_center=body.cost_center,
        department=body.department,
        project=body.project,
        po_number=body.po_number,
        payment_terms=body.payment_terms,
        cadence=body.cadence.value,
        day_of_period=body.day_of_period,
        start_date=body.start_date,
        end_date=body.end_date,
        variance_tolerance_pct=body.variance_tolerance_pct,
        notes=body.notes,
        status=STATUS_ACTIVE,
    )
    # First occurrence on/after start_date matching day_of_period.
    _seed_next_run_on(template, after=body.start_date)
    db.add(template)
    await db.flush()
    await dispatch_audit(
        db,
        correlation_id=uuid.uuid4(),
        organization_id=org_id,
        actor_id=user.id,
        action="recurring_template.created",
        entity_type="recurring_invoice_template",
        entity_id=template.id,
        details={"name": template.name, "cadence": template.cadence},
    )
    await db.commit()
    await db.refresh(template)
    return RecurringTemplateResponse.from_db(template)


@router.get("/{template_id}", response_model=RecurringTemplateResponse)
async def get_template(
    template_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(*_READ_ROLES)),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    template = await _get_scoped(db, template_id, entity_id)
    return RecurringTemplateResponse.from_db(template)


@router.patch("/{template_id}", response_model=RecurringTemplateResponse)
async def update_template(
    template_id: uuid.UUID,
    body: RecurringTemplateUpdate,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(*_WRITE_ROLES)),
    org_id: uuid.UUID = Depends(get_org_id),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    template = await _get_scoped(db, template_id, entity_id)
    data = body.model_dump(exclude_unset=True)

    changed: list[str] = []
    for field, value in data.items():
        if field == "vendor_id":
            new_vendor = uuid.UUID(value) if value else None
            if new_vendor != template.vendor_id:
                template.vendor_id = new_vendor
                template.vendor_name = await _resolve_vendor_name(db, new_vendor)
                changed.append("vendor_id")
            continue
        if field == "cadence":
            value = value.value if hasattr(value, "value") else value
        if field == "currency" and value:
            value = value.upper()
        if getattr(template, field) != value:
            setattr(template, field, value)
            changed.append(field)

    # Recompute next_run_on if a schedule-shaping field changed AND the template
    # is still active (paused/ended templates don't carry a live cursor).
    if template.status == STATUS_ACTIVE and (_SCHEDULE_FIELDS & set(changed)):
        _seed_next_run_on(template, after=max(date.today(), template.start_date))

    if changed:
        await dispatch_audit(
            db,
            correlation_id=uuid.uuid4(),
            organization_id=org_id,
            actor_id=user.id,
            action="recurring_template.updated",
            entity_type="recurring_invoice_template",
            entity_id=template.id,
            details={"changed": sorted(changed)},
        )
    await db.commit()
    await db.refresh(template)
    return RecurringTemplateResponse.from_db(template)


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(
    template_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(*_WRITE_ROLES)),
    org_id: uuid.UUID = Depends(get_org_id),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    template = await _get_scoped(db, template_id, entity_id)
    generated = (
        await db.execute(select(func.count()).where(Invoice.recurring_template_id == template.id))
    ).scalar() or 0
    if generated > 0:
        raise HTTPException(
            status_code=409,
            detail="Template has generated invoices; end it instead of deleting.",
        )
    await dispatch_audit(
        db,
        correlation_id=uuid.uuid4(),
        organization_id=org_id,
        actor_id=user.id,
        action="recurring_template.deleted",
        entity_type="recurring_invoice_template",
        entity_id=template.id,
        details={"name": template.name},
    )
    await db.delete(template)
    await db.commit()


# --------------------------------------------------------------------------- #
# Lifecycle — pause / resume / end
# --------------------------------------------------------------------------- #


async def _audit_lifecycle(
    db: AsyncSession,
    template: RecurringInvoiceTemplate,
    org_id: uuid.UUID,
    actor_id: uuid.UUID,
    action: str,
) -> None:
    await dispatch_audit(
        db,
        correlation_id=uuid.uuid4(),
        organization_id=org_id,
        actor_id=actor_id,
        action=action,
        entity_type="recurring_invoice_template",
        entity_id=template.id,
        details={"status": template.status},
    )


@router.post("/{template_id}/pause", response_model=RecurringTemplateResponse)
async def pause_template(
    template_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(*_WRITE_ROLES)),
    org_id: uuid.UUID = Depends(get_org_id),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    template = await _get_scoped(db, template_id, entity_id)
    if template.status != STATUS_ACTIVE:
        raise HTTPException(status_code=409, detail="Only an active template can be paused")
    template.status = STATUS_PAUSED
    await _audit_lifecycle(db, template, org_id, user.id, "recurring_template.paused")
    await db.commit()
    await db.refresh(template)
    return RecurringTemplateResponse.from_db(template)


@router.post("/{template_id}/resume", response_model=RecurringTemplateResponse)
async def resume_template(
    template_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(*_WRITE_ROLES)),
    org_id: uuid.UUID = Depends(get_org_id),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    template = await _get_scoped(db, template_id, entity_id)
    if template.status != STATUS_PAUSED:
        raise HTTPException(status_code=409, detail="Only a paused template can be resumed")
    template.status = STATUS_ACTIVE
    # Re-anchor from today so resuming never back-fires every historic period
    # the template slept through.
    _seed_next_run_on(template, after=max(date.today(), template.start_date))
    await _audit_lifecycle(db, template, org_id, user.id, "recurring_template.resumed")
    await db.commit()
    await db.refresh(template)
    return RecurringTemplateResponse.from_db(template)


@router.post("/{template_id}/end", response_model=RecurringTemplateResponse)
async def end_template(
    template_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(*_WRITE_ROLES)),
    org_id: uuid.UUID = Depends(get_org_id),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    template = await _get_scoped(db, template_id, entity_id)
    if template.status == STATUS_ENDED:
        raise HTTPException(status_code=409, detail="Template is already ended")
    template.status = STATUS_ENDED
    template.next_run_on = None  # terminal — nothing pending
    await _audit_lifecycle(db, template, org_id, user.id, "recurring_template.ended")
    await db.commit()
    await db.refresh(template)
    return RecurringTemplateResponse.from_db(template)


# --------------------------------------------------------------------------- #
# Generate-now (idempotent) / upcoming schedule / history
# --------------------------------------------------------------------------- #


@router.post("/{template_id}/generate-now", status_code=status.HTTP_201_CREATED)
async def generate_now(
    template_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(*_WRITE_ROLES)),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    template = await _get_scoped(db, template_id, entity_id)
    if template.status == STATUS_ENDED:
        raise HTTPException(status_code=409, detail="Cannot generate from an ended template")
    if template.amount is None or not template.vendor_name:
        raise HTTPException(
            status_code=422,
            detail="Template needs a vendor and amount before it can generate an invoice",
        )

    # The current due period — a pure function of (schedule, today), NOT the
    # mutable next_run_on cursor, so a re-call on the same day always resolves
    # to the SAME period and short-circuits to the existing invoice (idempotent
    # regardless of how far the sweep has since advanced the cursor).
    run_on = svc.current_due_run_on(
        template.cadence,
        template.day_of_period,
        today=date.today(),
        start_date=template.start_date,
    )
    period_key = svc.period_key_for(template.cadence, run_on)

    # Pre-check for an already-generated period → idempotent 200, no duplicate.
    existing = (
        await db.execute(
            select(Invoice).where(
                Invoice.recurring_template_id == template.id,
                Invoice.recurring_period_key == period_key,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return Response(
            content=_generate_payload(existing, period_key),
            media_type="application/json",
            status_code=200,
        )

    invoice = await svc.generate_one(db, template, run_on=run_on, actor_id=user.id)
    if invoice is None:
        raise HTTPException(status_code=422, detail="Template could not generate an invoice")
    await db.commit()
    await db.refresh(invoice)
    return Response(
        content=_generate_payload(invoice, invoice.recurring_period_key or period_key),
        media_type="application/json",
        status_code=201,
    )


def _generate_payload(invoice: Invoice, period_key: str) -> str:
    return json.dumps(
        {
            "invoice_id": str(invoice.id),
            "invoice_number": invoice.invoice_number,
            "period_key": period_key,
            "status": invoice.status.value
            if hasattr(invoice.status, "value")
            else str(invoice.status),
        }
    )


@router.get("/{template_id}/upcoming-schedule", response_model=UpcomingScheduleResponse)
async def upcoming_schedule(
    template_id: uuid.UUID,
    count: int = Query(6, ge=1, le=60),
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(*_READ_ROLES)),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    template = await _get_scoped(db, template_id, entity_id)
    occurrences = svc.project_schedule(template, count=count)
    return UpcomingScheduleResponse(
        template_id=str(template.id),
        occurrences=[
            ScheduleOccurrence(
                period_key=pk,
                run_on=run_on.isoformat(),
                amount=template.amount,
                currency=template.currency,
            )
            for pk, run_on in occurrences
        ],
    )


@router.get("/{template_id}/history", response_model=GeneratedHistoryResponse)
async def template_history(
    template_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(*_READ_ROLES)),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    template = await _get_scoped(db, template_id, entity_id)
    rows = list(
        (
            await db.execute(
                select(Invoice)
                .where(Invoice.recurring_template_id == template.id)
                .order_by(Invoice.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    items = [
        GeneratedInvoiceItem(
            invoice_id=str(inv.id),
            invoice_number=inv.invoice_number,
            period_key=inv.recurring_period_key,
            amount=inv.amount,
            currency=inv.currency,
            status=inv.status.value if hasattr(inv.status, "value") else str(inv.status),
            created_at=inv.created_at.isoformat() if inv.created_at else "",
        )
        for inv in rows
    ]
    return GeneratedHistoryResponse(template_id=str(template.id), items=items, total=len(items))
