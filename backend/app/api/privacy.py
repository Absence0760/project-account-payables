"""GDPR / CCPA privacy endpoints (`/api/privacy`) — DSAR export + erasure.

Two coupled data-subject rights, both **admin-only** (the privacy-officer
privilege) and both audited into the tenant's append-only trail:

- ``POST /privacy/dsar`` — assemble everything held about a data subject into a
  portable JSON bundle (GDPR Art. 15 / CCPA right-to-know). Audited
  ``privacy.dsar_export``.
- ``POST /privacy/erasure`` — irreversibly redact the subject's PII while
  PRESERVING the immutable financial + audit record (GDPR Art. 17 / CCPA
  right-to-delete). Legally-required retention wins for transactional rows: we
  redact PII text fields and keep the money trail. Audited ``privacy.erasure``.
  Idempotent — re-running on an already-erased subject is a safe no-op.
- ``GET /privacy/requests`` — the privacy officer's request history (PII-free).

Subjects span the control plane (``User``) and the tenant DB (``VendorUser``,
``Vendor`` contacts). Tenant isolation is enforced by the injected ``get_tenant``
/ ``get_tenant_db`` chokepoint (which cross-checks the JWT ``org`` claim) plus
filtering every query by ``organization_id`` — a DSAR / erasure for one subject
can never reach another subject's or another tenant's data.

**PII-out-of-logs:** the audit row and the persisted ``DataSubjectRequest`` row
record only the resolved subject UUID + type + non-identifying counts — never the
raw email / tax-id / bank details. The DSAR bundle itself is returned in the HTTP
response and never written to a log or to the request-tracking table.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ROLE_ADMIN, require_roles
from app.database import get_control_db
from app.models.data_subject_request import (
    REQUEST_DSAR_EXPORT,
    REQUEST_ERASURE,
    STATUS_COMPLETED,
    STATUS_NOOP,
    SUBJECT_TYPES,
    DataSubjectRequest,
)
from app.models.organization import Organization
from app.models.user import User
from app.schemas.privacy import (
    DataSubjectRequestList,
    DataSubjectRequestSummary,
    DSARRequest,
    DSARResponse,
    ErasureRequest,
    ErasureResponse,
)
from app.services.audit_dispatch import dispatch_audit
from app.services.privacy_erasure import erase_subject
from app.services.privacy_export import (
    SubjectNotFound,
    build_dsar_bundle,
    resolve_subject_id,
)
from app.tenant import get_tenant, get_tenant_db

router = APIRouter(prefix="/privacy", tags=["privacy"])


def _validate_subject_type(subject_type: str) -> None:
    if subject_type not in SUBJECT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown subject_type '{subject_type}'; valid: {list(SUBJECT_TYPES)}",
        )


@router.post("/dsar", response_model=DSARResponse)
async def dsar_export(
    body: DSARRequest,
    org: Organization = Depends(get_tenant),
    db: AsyncSession = Depends(get_tenant_db),
    control_db: AsyncSession = Depends(get_control_db),
    user: User = Depends(require_roles(ROLE_ADMIN)),
):
    """Assemble a portable bundle of everything held about a data subject.

    Admin only. The request itself is audited (``privacy.dsar_export``) and
    recorded in ``data_subject_requests`` — both PII-free (subject UUID + type +
    counts only). The bundle is returned in the body, never logged or stored.
    """
    _validate_subject_type(body.subject_type)
    now = datetime.now(UTC)

    try:
        subject_id = await resolve_subject_id(
            subject_type=body.subject_type,
            identifier=body.identifier,
            organization_id=org.id,
            control_db=control_db,
            tenant_db=db,
        )
        bundle = await build_dsar_bundle(
            subject_type=body.subject_type,
            subject_id=subject_id,
            organization_id=org.id,
            control_db=control_db,
            tenant_db=db,
        )
    except SubjectNotFound as exc:
        # Same shape regardless of WHY (wrong tenant vs. truly absent) so the
        # response can't be used to probe which subjects exist in other tenants.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Subject not found"
        ) from exc

    request_row = DataSubjectRequest(
        id=uuid.uuid4(),
        organization_id=org.id,
        request_type=REQUEST_DSAR_EXPORT,
        subject_type=body.subject_type,
        subject_id=subject_id,
        status=STATUS_COMPLETED,
        requested_by=user.id,
        completed_at=now,
        record_counts=bundle.get("counts"),
    )
    db.add(request_row)

    # Append-only audit row — PII-free: subject UUID + type, never the email /
    # tax-id / bank details that the bundle itself carries.
    await dispatch_audit(
        db,
        correlation_id=uuid.uuid4(),
        organization_id=org.id,
        actor_id=user.id,
        action="privacy.dsar_export",
        entity_type="data_subject_request",
        entity_id=request_row.id,
        details={
            "subject_type": body.subject_type,
            "subject_id": str(subject_id),
        },
    )
    await db.commit()

    return DSARResponse(
        request_id=str(request_row.id),
        subject_type=body.subject_type,
        subject_id=str(subject_id),
        generated_at=now.isoformat(),
        data=bundle,
    )


@router.post("/erasure", response_model=ErasureResponse)
async def erasure(
    body: ErasureRequest,
    org: Organization = Depends(get_tenant),
    db: AsyncSession = Depends(get_tenant_db),
    control_db: AsyncSession = Depends(get_control_db),
    user: User = Depends(require_roles(ROLE_ADMIN)),
):
    """Irreversibly redact a subject's PII; preserve the financial + audit trail.

    Admin only. ``confirm`` must be true. Redacts PII text fields in place and
    NEVER touches a money field or an ``audit_log`` row (it writes a NEW audit
    row instead). Idempotent — re-running on an already-erased subject returns a
    ``noop`` status with no further change.
    """
    _validate_subject_type(body.subject_type)
    if not body.confirm:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="confirm must be true to perform an erasure",
        )
    now = datetime.now(UTC)

    try:
        subject_id = await resolve_subject_id(
            subject_type=body.subject_type,
            identifier=body.identifier,
            organization_id=org.id,
            control_db=control_db,
            tenant_db=db,
        )
        result = await erase_subject(
            subject_type=body.subject_type,
            subject_id=subject_id,
            organization_id=org.id,
            control_db=control_db,
            tenant_db=db,
            now=now,
        )
    except SubjectNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Subject not found"
        ) from exc

    req_status = STATUS_NOOP if result.already_erased else STATUS_COMPLETED

    request_row = DataSubjectRequest(
        id=uuid.uuid4(),
        organization_id=org.id,
        request_type=REQUEST_ERASURE,
        subject_type=body.subject_type,
        subject_id=subject_id,
        status=req_status,
        requested_by=user.id,
        completed_at=now,
        record_counts=result.record_counts,
        fields_redacted=result.fields_redacted,
        note=body.note,
    )
    db.add(request_row)

    # Append-only audit row. PII-free: subject UUID + type + counts only.
    await dispatch_audit(
        db,
        correlation_id=uuid.uuid4(),
        organization_id=org.id,
        actor_id=user.id,
        action="privacy.erasure",
        entity_type="data_subject_request",
        entity_id=request_row.id,
        details={
            "subject_type": body.subject_type,
            "subject_id": str(subject_id),
            "status": req_status,
            "fields_redacted": result.fields_redacted,
        },
    )
    # Cross-DB write without 2PC (control plane + tenant DB). Commit the
    # tenant-side audit + request rows FIRST, then the control-plane PII
    # mutation. If the control commit then fails, the subject is NOT yet erased
    # (control rolls back) and a re-run re-attempts cleanly. Committing control
    # first would, on a tenant-commit failure, leave the PII erased with no audit
    # evidence — and the idempotency tombstone would suppress the audit on every
    # retry. An append-only audit row that slightly precedes its
    # (retried-to-success) mutation is the safe trade; the reverse loses the
    # regulated record permanently. For vendor_* subjects control_db has no
    # pending change, so its commit is a harmless no-op.
    await db.commit()
    await control_db.commit()

    return ErasureResponse(
        request_id=str(request_row.id),
        subject_type=body.subject_type,
        subject_id=str(subject_id),
        status=req_status,
        already_erased=result.already_erased,
        fields_redacted=result.fields_redacted,
        record_counts=result.record_counts,
        completed_at=now.isoformat(),
    )


@router.get("/requests", response_model=DataSubjectRequestList)
async def list_requests(
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(ROLE_ADMIN)),
):
    """The privacy officer's request history for this tenant (PII-free)."""
    rows = (
        (
            await db.execute(
                select(DataSubjectRequest)
                .where(DataSubjectRequest.organization_id == org.id)
                .order_by(DataSubjectRequest.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return DataSubjectRequestList(
        total=len(rows),
        requests=[DataSubjectRequestSummary.from_row(r) for r in rows],
    )
