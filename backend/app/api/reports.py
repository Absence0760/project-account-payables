"""Custom (ad-hoc) report builder — ``/api/reports`` router.

Exposes the report-builder catalog, saved-definition CRUD, ad-hoc + saved runs,
and branded CSV/PDF export. The safe query engine + the key→column whitelist
live in ``app/services/report_builder.py`` (the security boundary); this router
is the HTTP surface + persistence + RBAC + audit.

- **Reads** (catalog / list / get / run) — all four roles.
- **Mutations** (save / update / delete) — admin / ap_manager / cfo; every
  mutation writes a PII-free audit row.
- Runs go through the ``get_tenant`` chokepoint (tenant isolation) and honour
  ``X-Entity-ID`` entity scoping like the rest of analytics.

Money is always an exact decimal string on the wire. See
``backend/docs/report-builder.md``.
"""

from __future__ import annotations

import io
import uuid
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    ROLE_ADMIN,
    ROLE_AP_CLERK,
    ROLE_AP_MANAGER,
    ROLE_CFO,
    get_org_id,
    require_roles,
)
from app.models.organization import Organization
from app.models.report_definition import ReportDefinition
from app.models.user import User
from app.schemas.report import (
    CatalogResponse,
    ReportDefinitionResponse,
    ReportListResponse,
    ReportResult,
    ReportRunRequest,
    ReportSaveRequest,
    ReportSpec,
    ReportUpdateRequest,
)
from app.services.audit_dispatch import dispatch_audit
from app.services.report_builder import (
    ReportValidationError,
    build_catalog,
    compile_spec,
    run_report,
)
from app.tenant import (
    apply_entity_scope,
    get_entity_id,
    get_tenant,
    get_tenant_db,
    get_write_entity_id,
)

router = APIRouter(prefix="/reports", tags=["reports"])

_READ_ROLES = (ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_AP_CLERK, ROLE_CFO)
_WRITE_ROLES = (ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)

# Cap on rows materialised into an export file (no per-page slicing on a
# download — the operator wants the whole result set, but bounded).
_EXPORT_MAX_ROWS = 1000


def _validate_spec_or_422(spec: ReportSpec) -> None:
    """Compile the spec against the catalog purely to validate it (no DB).
    Raises 422 on any out-of-catalog reference."""
    try:
        compile_spec(spec)
    except ReportValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


async def _get_scoped(
    db: AsyncSession, report_id: uuid.UUID, entity_id: uuid.UUID | None
) -> ReportDefinition:
    q = apply_entity_scope(
        select(ReportDefinition).where(ReportDefinition.id == report_id),
        ReportDefinition,
        entity_id,
    )
    row = (await db.execute(q)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Report not found")
    return row


def _spec_from_row(row: ReportDefinition) -> ReportSpec:
    return ReportSpec(
        data_source=row.data_source,
        dimensions=row.dimensions or [],
        measures=row.measures or [],
        filters=row.filters or [],
        sort=row.sort or [],
        limit=row.row_limit,
    )


# --------------------------------------------------------------------------- #
# Catalog
# --------------------------------------------------------------------------- #
@router.get("/catalog", response_model=CatalogResponse)
async def get_catalog(
    user: User = Depends(require_roles(*_READ_ROLES)),
):
    """The report-builder catalog — every data source with its allowed
    dimensions, measures, and filters. This is the ONLY contract between the
    client and the query engine: the client picks keys from here."""
    return build_catalog()


# --------------------------------------------------------------------------- #
# Ad-hoc run (not saved)
# --------------------------------------------------------------------------- #
@router.post("/run", response_model=ReportResult)
async def run_adhoc(
    body: ReportRunRequest,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(*_READ_ROLES)),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    """Run an ad-hoc spec without persisting it."""
    try:
        return await run_report(
            db, body, entity_id=entity_id, page=body.page, page_size=body.page_size
        )
    except ReportValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


# --------------------------------------------------------------------------- #
# Saved-definition list / create
# --------------------------------------------------------------------------- #
@router.get("", response_model=ReportListResponse)
async def list_reports(
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(*_READ_ROLES)),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    q = apply_entity_scope(
        select(ReportDefinition).order_by(ReportDefinition.created_at.desc()),
        ReportDefinition,
        entity_id,
    )
    rows = list((await db.execute(q)).scalars().all())
    return ReportListResponse(reports=[ReportDefinitionResponse.from_db(r) for r in rows])


@router.post("", response_model=ReportDefinitionResponse, status_code=status.HTTP_201_CREATED)
async def create_report(
    body: ReportSaveRequest,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(*_WRITE_ROLES)),
    org_id: uuid.UUID = Depends(get_org_id),
    entity_id: uuid.UUID = Depends(get_write_entity_id),
):
    # Validate the spec against the catalog before persisting.
    spec = ReportSpec(
        data_source=body.data_source,
        dimensions=body.dimensions,
        measures=body.measures,
        filters=body.filters,
        sort=body.sort,
        limit=body.limit,
    )
    _validate_spec_or_422(spec)

    row = ReportDefinition(
        organization_id=org_id,
        entity_id=entity_id,
        name=body.name,
        description=body.description,
        data_source=body.data_source,
        dimensions=[d.model_dump() for d in body.dimensions],
        measures=[m.model_dump() for m in body.measures],
        filters=[f.model_dump() for f in body.filters],
        sort=[s.model_dump() for s in body.sort],
        row_limit=body.limit,
        created_by_user_id=user.id,
    )
    db.add(row)
    await db.flush()
    await dispatch_audit(
        db,
        correlation_id=uuid.uuid4(),
        organization_id=org_id,
        actor_id=user.id,
        action="report.created",
        entity_type="report_definition",
        entity_id=row.id,
        details={"name": row.name, "data_source": row.data_source},
    )
    await db.commit()
    await db.refresh(row)
    return ReportDefinitionResponse.from_db(row)


# --------------------------------------------------------------------------- #
# Saved-definition detail / update / delete
# --------------------------------------------------------------------------- #
@router.get("/{report_id}", response_model=ReportDefinitionResponse)
async def get_report(
    report_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(*_READ_ROLES)),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    row = await _get_scoped(db, report_id, entity_id)
    return ReportDefinitionResponse.from_db(row)


@router.patch("/{report_id}", response_model=ReportDefinitionResponse)
async def update_report(
    report_id: uuid.UUID,
    body: ReportUpdateRequest,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(*_WRITE_ROLES)),
    org_id: uuid.UUID = Depends(get_org_id),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    row = await _get_scoped(db, report_id, entity_id)
    data = body.model_dump(exclude_unset=True)

    if "name" in data and data["name"] is not None:
        row.name = data["name"]
    if "description" in data:
        row.description = data["description"]
    if "data_source" in data and data["data_source"] is not None:
        row.data_source = data["data_source"]
    if "dimensions" in data and data["dimensions"] is not None:
        row.dimensions = [d.model_dump() for d in body.dimensions]
    if "measures" in data and data["measures"] is not None:
        row.measures = [m.model_dump() for m in body.measures]
    if "filters" in data and data["filters"] is not None:
        row.filters = [f.model_dump() for f in body.filters]
    if "sort" in data and data["sort"] is not None:
        row.sort = [s.model_dump() for s in body.sort]
    if "limit" in data:
        row.row_limit = data["limit"]

    # Re-validate the resulting spec against the catalog.
    _validate_spec_or_422(_spec_from_row(row))

    await dispatch_audit(
        db,
        correlation_id=uuid.uuid4(),
        organization_id=org_id,
        actor_id=user.id,
        action="report.updated",
        entity_type="report_definition",
        entity_id=row.id,
        details={"name": row.name, "data_source": row.data_source},
    )
    await db.commit()
    await db.refresh(row)
    return ReportDefinitionResponse.from_db(row)


@router.delete("/{report_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_report(
    report_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(*_WRITE_ROLES)),
    org_id: uuid.UUID = Depends(get_org_id),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    row = await _get_scoped(db, report_id, entity_id)
    await dispatch_audit(
        db,
        correlation_id=uuid.uuid4(),
        organization_id=org_id,
        actor_id=user.id,
        action="report.deleted",
        entity_type="report_definition",
        entity_id=row.id,
        details={"name": row.name, "data_source": row.data_source},
    )
    await db.delete(row)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- #
# Run a saved report
# --------------------------------------------------------------------------- #
@router.post("/{report_id}/run", response_model=ReportResult)
async def run_saved(
    report_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(*_READ_ROLES)),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    row = await _get_scoped(db, report_id, entity_id)
    spec = _spec_from_row(row)
    try:
        return await run_report(db, spec, entity_id=entity_id, page=page, page_size=page_size)
    except ReportValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


# --------------------------------------------------------------------------- #
# Export a saved report (branded CSV / PDF)
# --------------------------------------------------------------------------- #
@router.get("/{report_id}/export")
async def export_report(
    report_id: uuid.UUID,
    format: str = Query("csv", pattern="^(csv|pdf)$"),
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(*_READ_ROLES)),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    """Download a saved report as a branded CSV or PDF. Reuses the shared brand
    provenance-header (CSV) + branded analytics-report PDF helpers, so the
    white-label chrome matches every other export surface."""
    from app.services.branding import get_brand_context
    from app.services.report_export import brand_provenance_header, safe_csv_writer

    row = await _get_scoped(db, report_id, entity_id)
    spec = _spec_from_row(row)
    try:
        result = await run_report(db, spec, entity_id=entity_id, page=1, page_size=_EXPORT_MAX_ROWS)
    except ReportValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    columns = result["columns"]
    header = [c["label"] for c in columns]
    keys = [c["key"] for c in columns]
    data_rows = [["" if r.get(k) is None else str(r.get(k)) for k in keys] for r in result["rows"]]

    brand = get_brand_context(org.settings if org else None)
    generated_at = datetime.now(UTC)
    safe_name = row.name.replace(" ", "_").replace("/", "_") or "report"

    if format == "pdf":
        from app.services.analytics_report_pdf import (
            AnalyticsReportContext,
            render_analytics_report_pdf,
        )

        ctx = AnalyticsReportContext(
            title=row.name,
            org_name=(org.name if org else "Organization"),
            period_label=f"Custom report — {row.data_source}",
            generated_at=generated_at,
            header=header,
            rows=data_rows,
            brand=brand,
        )
        pdf_bytes = render_analytics_report_pdf(ctx)
        filename = f"{safe_name}_{date.today().isoformat()}.pdf"
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # CSV: brand provenance comment block, then the data grid.
    buf = io.StringIO()
    writer = safe_csv_writer(buf)
    writer.writerow(header)
    for r in data_rows:
        writer.writerow(r)
    branded_csv = (
        brand_provenance_header(
            brand,
            org_name=(org.name if org else None),
            report=row.name,
            generated_at=generated_at,
        )
        + buf.getvalue()
    )
    filename = f"{safe_name}_{date.today().isoformat()}.csv"
    return Response(
        content=branded_csv,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
