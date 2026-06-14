"""Expense management endpoints — out-of-pocket / card expenses and the reports
that group them for approval + reimbursement.

WF1 (foundation) wires two routers: ``/expenses`` (CRUD + receipt upload /
download proxy) and ``/expense-reports`` (CRUD + attach/detach with
total-amount recompute). Policies, pre-approvals, and card-transaction
reconciliation land in later workflows (WF2-4); their models + schemas already
exist. See ``backend/docs/expense-management.md``.
"""

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import (
    ROLE_ADMIN,
    ROLE_AP_CLERK,
    ROLE_AP_MANAGER,
    ROLE_CFO,
    get_current_user,
    get_org_id,
    require_roles,
)
from app.api.pagination import PaginationParams, pagination_params
from app.models.expense import Expense, ExpenseReport
from app.models.user import User
from app.schemas.expense import (
    ExpenseCreate,
    ExpenseListResponse,
    ExpenseReportAttach,
    ExpenseReportCreate,
    ExpenseReportListResponse,
    ExpenseReportResponse,
    ExpenseReportUpdate,
    ExpenseResponse,
    ExpenseUpdate,
)
from app.services.audit_dispatch import dispatch_audit
from app.services.storage import get_file, upload_expense_receipt
from app.tenant import (
    apply_entity_scope,
    get_entity_id,
    get_tenant_db,
    get_write_entity_id,
)

router = APIRouter(prefix="/expenses", tags=["expenses"])
reports_router = APIRouter(prefix="/expense-reports", tags=["expense-reports"])

# Fields a PATCH on an expense may touch (status excluded — owned by later flows).
_EXPENSE_UPDATABLE_FIELDS = (
    "expense_date",
    "merchant",
    "category",
    "description",
    "amount",
    "currency",
    "payment_method",
    "reimbursable",
    "mileage_miles",
)
_REPORT_UPDATABLE_FIELDS = (
    "report_number",
    "title",
    "currency",
    "notes",
)


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def _receipt_url(file_key: str | None) -> str | None:
    return f"/api/expenses/receipt/{file_key}" if file_key else None


def _to_response(e: Expense) -> ExpenseResponse:
    return ExpenseResponse(
        id=str(e.id),
        report_id=str(e.report_id) if e.report_id else None,
        expense_date=e.expense_date.isoformat() if e.expense_date else "",
        merchant=e.merchant,
        category=e.category,
        description=e.description,
        amount=float(e.amount),
        currency=e.currency,
        gl_account_id=str(e.gl_account_id) if e.gl_account_id else None,
        receipt_file_key=e.receipt_file_key,
        receipt_url=_receipt_url(e.receipt_file_key),
        payment_method=str(e.payment_method),
        card_transaction_id=str(e.card_transaction_id) if e.card_transaction_id else None,
        policy_violations=e.policy_violations,
        status=str(e.status),
        reimbursable=e.reimbursable,
        mileage_miles=float(e.mileage_miles) if e.mileage_miles is not None else None,
        created_at=e.created_at.isoformat() if e.created_at else "",
        updated_at=e.updated_at.isoformat() if e.updated_at else "",
    )


def _report_to_response(r: ExpenseReport) -> ExpenseReportResponse:
    return ExpenseReportResponse(
        id=str(r.id),
        report_number=r.report_number,
        title=r.title,
        employee_user_id=str(r.employee_user_id),
        status=str(r.status),
        submitted_at=r.submitted_at.isoformat() if r.submitted_at else None,
        approved_at=r.approved_at.isoformat() if r.approved_at else None,
        approved_by=str(r.approved_by) if r.approved_by else None,
        total_amount=float(r.total_amount),
        currency=r.currency,
        notes=r.notes,
        expenses=[_to_response(e) for e in sorted(r.expenses, key=lambda x: x.expense_date)],
        created_at=r.created_at.isoformat() if r.created_at else "",
        updated_at=r.updated_at.isoformat() if r.updated_at else "",
    )


async def _get_expense_or_404(db: AsyncSession, expense_id: uuid.UUID) -> Expense:
    expense = (
        await db.execute(select(Expense).where(Expense.id == expense_id))
    ).scalar_one_or_none()
    if not expense:
        raise HTTPException(status_code=404, detail="Expense not found")
    return expense


async def _get_report_or_404(db: AsyncSession, report_id: uuid.UUID) -> ExpenseReport:
    report = (
        await db.execute(
            select(ExpenseReport)
            .where(ExpenseReport.id == report_id)
            # populate_existing so a report already in the identity map has its
            # `expenses` collection refreshed from the DB — without it, a second
            # fetch in the same session (e.g. after an attach mutation +
            # re-fetch) would return the stale, previously-loaded collection.
            .execution_options(populate_existing=True)
            .options(selectinload(ExpenseReport.expenses))
        )
    ).scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="Expense report not found")
    return report


async def _recompute_report_total(db: AsyncSession, report: ExpenseReport) -> None:
    """Recompute ``total_amount`` from the report's currently-attached expenses.

    Money stays exact: the SUM runs in Postgres over ``Numeric(15, 2)`` and the
    result is coerced to ``Decimal`` (never float)."""
    total = (
        await db.execute(
            select(func.coalesce(func.sum(Expense.amount), 0)).where(
                Expense.report_id == report.id
            )
        )
    ).scalar_one()
    report.total_amount = Decimal(total)


# ---------------------------------------------------------------------------
# Expenses — list + create
# ---------------------------------------------------------------------------


@router.get("", response_model=ExpenseListResponse)
async def list_expenses(
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_AP_CLERK, ROLE_CFO)),
    status_filter: str | None = Query(None, alias="status"),
    report_id: uuid.UUID | None = Query(None),
    pagination: PaginationParams = Depends(pagination_params),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    base = apply_entity_scope(select(Expense), Expense, entity_id)
    if status_filter:
        base = base.where(Expense.status == status_filter)
    if report_id:
        base = base.where(Expense.report_id == report_id)

    total = int(
        (await db.execute(select(func.count()).select_from(base.subquery()))).scalar() or 0
    )
    paged = (
        base.order_by(Expense.created_at.desc())
        .offset(pagination.offset)
        .limit(pagination.limit)
    )
    rows = (await db.execute(paged)).scalars().all()
    return ExpenseListResponse(
        items=[_to_response(e) for e in rows],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.post("", response_model=ExpenseResponse, status_code=status.HTTP_201_CREATED)
async def create_expense(
    body: ExpenseCreate,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_AP_CLERK)),
    org_id: uuid.UUID = Depends(get_org_id),
    entity_id: uuid.UUID = Depends(get_write_entity_id),
):
    gl_uuid: uuid.UUID | None = None
    if body.gl_account_id:
        try:
            gl_uuid = uuid.UUID(body.gl_account_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid gl_account_id")

    report_uuid: uuid.UUID | None = None
    if body.report_id:
        try:
            report_uuid = uuid.UUID(body.report_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid report_id")
        report = await _get_report_or_404(db, report_uuid)
    else:
        report = None

    expense = Expense(
        report_id=report_uuid,
        expense_date=body.expense_date,
        merchant=body.merchant,
        category=body.category,
        description=body.description,
        amount=body.amount,
        currency=body.currency,
        gl_account_id=gl_uuid,
        payment_method=body.payment_method,
        reimbursable=body.reimbursable,
        mileage_miles=body.mileage_miles,
        organization_id=org_id,
        entity_id=entity_id,
    )
    db.add(expense)
    await db.flush()

    if report is not None:
        await _recompute_report_total(db, report)

    await dispatch_audit(
        db,
        correlation_id=uuid.uuid4(),
        organization_id=org_id,
        actor_id=user.id,
        action="expense.created",
        entity_type="expense",
        entity_id=expense.id,
        details={"amount": str(expense.amount), "category": expense.category},
    )
    await db.commit()
    fresh = await _get_expense_or_404(db, expense.id)
    return _to_response(fresh)


# ---------------------------------------------------------------------------
# Receipt upload + download proxy — declared BEFORE /{expense_id} so the
# literal `receipt` segment isn't captured as an {expense_id} UUID.
# ---------------------------------------------------------------------------


@router.get("/receipt/{file_key:path}")
async def get_expense_receipt(
    file_key: str,
    user: User = Depends(get_current_user),
):
    """Proxy a stored expense receipt from S3.

    Keys are stamped ``<org_id>/expenses/<expense_id>/<filename>`` at upload.
    The caller must belong to the org in the first segment — same 404 for
    wrong-org and missing-file so the response can't enumerate prefixes
    (mirrors the invoice / contract file endpoints).
    """
    prefix = file_key.split("/", 1)[0]
    if prefix != str(user.organization_id):
        raise HTTPException(status_code=404, detail="File not found")
    try:
        content, content_type = get_file(file_key)
    except Exception:
        raise HTTPException(status_code=404, detail="File not found")
    return Response(content=content, media_type=content_type)


@router.post("/{expense_id}/receipt", response_model=ExpenseResponse)
async def upload_receipt(
    expense_id: uuid.UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_AP_CLERK)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    expense = await _get_expense_or_404(db, expense_id)
    try:
        file_key, _file_url = await upload_expense_receipt(org_id, expense.id, file)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    expense.receipt_file_key = file_key
    await dispatch_audit(
        db,
        correlation_id=uuid.uuid4(),
        organization_id=org_id,
        actor_id=user.id,
        action="expense.receipt_uploaded",
        entity_type="expense",
        entity_id=expense.id,
        details={"file_key": file_key},
    )
    await db.commit()
    fresh = await _get_expense_or_404(db, expense.id)
    return _to_response(fresh)


# ---------------------------------------------------------------------------
# Expenses — get / patch / delete
# ---------------------------------------------------------------------------


@router.get("/{expense_id}", response_model=ExpenseResponse)
async def get_expense(
    expense_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_AP_CLERK, ROLE_CFO)),
):
    return _to_response(await _get_expense_or_404(db, expense_id))


@router.patch("/{expense_id}", response_model=ExpenseResponse)
async def update_expense(
    expense_id: uuid.UUID,
    body: ExpenseUpdate,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_AP_CLERK)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    expense = await _get_expense_or_404(db, expense_id)
    payload = body.model_dump(exclude_unset=True)

    # gl_account_id / report_id need UUID coercion; handle them explicitly.
    affected_reports: set[uuid.UUID] = set()
    if "gl_account_id" in payload:
        raw = payload.pop("gl_account_id")
        gl_uuid: uuid.UUID | None = None
        if raw is not None:
            try:
                gl_uuid = uuid.UUID(raw)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid gl_account_id")
        if expense.gl_account_id != gl_uuid:
            expense.gl_account_id = gl_uuid
    if "report_id" in payload:
        raw = payload.pop("report_id")
        new_report: uuid.UUID | None = None
        if raw is not None:
            try:
                new_report = uuid.UUID(raw)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid report_id")
            await _get_report_or_404(db, new_report)
        if expense.report_id != new_report:
            if expense.report_id:
                affected_reports.add(expense.report_id)
            if new_report:
                affected_reports.add(new_report)
            expense.report_id = new_report

    changed: list[str] = []
    for field in _EXPENSE_UPDATABLE_FIELDS:
        if field in payload and getattr(expense, field) != payload[field]:
            setattr(expense, field, payload[field])
            changed.append(field)

    # An amount change ripples into the owning report's total.
    if "amount" in changed and expense.report_id:
        affected_reports.add(expense.report_id)

    await db.flush()
    for rid in affected_reports:
        report = await _get_report_or_404(db, rid)
        await _recompute_report_total(db, report)

    if changed or affected_reports:
        await dispatch_audit(
            db,
            correlation_id=uuid.uuid4(),
            organization_id=org_id,
            actor_id=user.id,
            action="expense.updated",
            entity_type="expense",
            entity_id=expense.id,
            details={"fields": changed or ["report_id"]},
        )
    await db.commit()
    fresh = await _get_expense_or_404(db, expense.id)
    return _to_response(fresh)


@router.delete("/{expense_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_expense(
    expense_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_AP_CLERK)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    expense = await _get_expense_or_404(db, expense_id)
    owning_report = expense.report_id
    await dispatch_audit(
        db,
        correlation_id=uuid.uuid4(),
        organization_id=org_id,
        actor_id=user.id,
        action="expense.deleted",
        entity_type="expense",
        entity_id=expense.id,
        details={"amount": str(expense.amount)},
    )
    await db.delete(expense)
    await db.flush()
    if owning_report:
        report = await _get_report_or_404(db, owning_report)
        await _recompute_report_total(db, report)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Expense reports
# ---------------------------------------------------------------------------


@reports_router.get("", response_model=ExpenseReportListResponse)
async def list_reports(
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_AP_CLERK, ROLE_CFO)),
    status_filter: str | None = Query(None, alias="status"),
    pagination: PaginationParams = Depends(pagination_params),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    base = apply_entity_scope(select(ExpenseReport), ExpenseReport, entity_id)
    if status_filter:
        base = base.where(ExpenseReport.status == status_filter)

    total = int(
        (await db.execute(select(func.count()).select_from(base.subquery()))).scalar() or 0
    )
    paged = (
        base.options(selectinload(ExpenseReport.expenses))
        .order_by(ExpenseReport.created_at.desc())
        .offset(pagination.offset)
        .limit(pagination.limit)
    )
    rows = (await db.execute(paged)).scalars().all()
    return ExpenseReportListResponse(
        items=[_report_to_response(r) for r in rows],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@reports_router.post("", response_model=ExpenseReportResponse, status_code=status.HTTP_201_CREATED)
async def create_report(
    body: ExpenseReportCreate,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_AP_CLERK)),
    org_id: uuid.UUID = Depends(get_org_id),
    entity_id: uuid.UUID = Depends(get_write_entity_id),
):
    employee_uuid = user.id
    if body.employee_user_id:
        try:
            employee_uuid = uuid.UUID(body.employee_user_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid employee_user_id")

    report = ExpenseReport(
        report_number=body.report_number,
        title=body.title,
        employee_user_id=employee_uuid,
        currency=body.currency,
        notes=body.notes,
        total_amount=Decimal("0"),
        organization_id=org_id,
        entity_id=entity_id,
    )
    db.add(report)
    await db.flush()
    await dispatch_audit(
        db,
        correlation_id=uuid.uuid4(),
        organization_id=org_id,
        actor_id=user.id,
        action="expense_report.created",
        entity_type="expense_report",
        entity_id=report.id,
        details={"report_number": report.report_number},
    )
    await db.commit()
    fresh = await _get_report_or_404(db, report.id)
    return _report_to_response(fresh)


@reports_router.get("/{report_id}", response_model=ExpenseReportResponse)
async def get_report(
    report_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_AP_CLERK, ROLE_CFO)),
):
    return _report_to_response(await _get_report_or_404(db, report_id))


@reports_router.patch("/{report_id}", response_model=ExpenseReportResponse)
async def update_report(
    report_id: uuid.UUID,
    body: ExpenseReportUpdate,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_AP_CLERK)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    report = await _get_report_or_404(db, report_id)
    payload = body.model_dump(exclude_unset=True)
    changed: list[str] = []
    for field in _REPORT_UPDATABLE_FIELDS:
        if field in payload and getattr(report, field) != payload[field]:
            setattr(report, field, payload[field])
            changed.append(field)
    if changed:
        await dispatch_audit(
            db,
            correlation_id=uuid.uuid4(),
            organization_id=org_id,
            actor_id=user.id,
            action="expense_report.updated",
            entity_type="expense_report",
            entity_id=report.id,
            details={"fields": changed},
        )
    await db.commit()
    fresh = await _get_report_or_404(db, report.id)
    return _report_to_response(fresh)


@reports_router.post("/{report_id}/expenses", response_model=ExpenseReportResponse)
async def attach_expenses(
    report_id: uuid.UUID,
    body: ExpenseReportAttach,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_AP_CLERK)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    """Attach (or detach) expenses on a report and recompute its total.

    Tenant isolation comes from the per-tenant DB session — each id is looked
    up in this tenant's ``expenses`` table; an unknown / cross-tenant id is a
    404. Detaching nulls ``report_id`` (the expense outlives the report)."""
    report = await _get_report_or_404(db, report_id)

    expense_uuids: list[uuid.UUID] = []
    for raw in body.expense_ids:
        try:
            expense_uuids.append(uuid.UUID(raw))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid expense id: {raw}")

    # Track every *other* report an expense is moving off of, so its
    # total_amount is recomputed too — otherwise reassigning an expense from
    # report A to report B leaves A's Numeric total stale (matches the
    # affected-reports handling in update_expense).
    affected_reports: set[uuid.UUID] = set()
    for eid in expense_uuids:
        expense = await _get_expense_or_404(db, eid)
        if body.detach:
            if expense.report_id == report.id:
                expense.report_id = None
        else:
            if expense.report_id and expense.report_id != report.id:
                affected_reports.add(expense.report_id)
            expense.report_id = report.id

    await db.flush()
    await _recompute_report_total(db, report)
    for rid in affected_reports:
        other = await _get_report_or_404(db, rid)
        await _recompute_report_total(db, other)

    await dispatch_audit(
        db,
        correlation_id=uuid.uuid4(),
        organization_id=org_id,
        actor_id=user.id,
        action="expense_report.expenses_attached",
        entity_type="expense_report",
        entity_id=report.id,
        details={"count": len(expense_uuids), "detach": body.detach},
    )
    await db.commit()
    fresh = await _get_report_or_404(db, report.id)
    return _report_to_response(fresh)
