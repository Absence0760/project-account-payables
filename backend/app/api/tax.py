"""Tax endpoints — 1099 reporting, W-9 upload, TIN validation, 1099 form
generation + e-filing, and the 1099 vendor dashboard.

All endpoints are ``admin`` + ``ap_manager`` only. CFO can read (reports,
dashboard, form download) but not upload W-9s, run TIN validation, or file
1099s — those are data-ingest / compliance-mutation actions, not finance
review ones.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, date, datetime

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    ROLE_ADMIN,
    ROLE_AP_MANAGER,
    ROLE_CFO,
    get_org_id,
    require_roles,
)
from app.config import settings as app_settings
from app.models.organization import Organization
from app.models.tax_filing import Tax1099Filing
from app.models.user import User
from app.models.vendor import Vendor
from app.services.audit_dispatch import dispatch_audit
from app.services.branding import get_brand_context
from app.services.currency_conversion import resolve_reporting_currency
from app.services.storage import (
    ALLOWED_CONTENT_TYPES,
    _put_object,
    _safe_filename,
)
from app.services.tax_1099 import build_1099_dashboard, build_1099_report
from app.services.tax_1099_forms import (
    FORM_MISC,
    FORM_NEC,
    build_form_context,
    render_1099_pdf,
)
from app.services.tax_filing_adapters import FilingFormPayload, get_tax_filing_adapter
from app.services.tin_validation_adapters import get_tin_validation_adapter
from app.tenant import get_tenant, get_tenant_db
from app.utils.dates import utc_today

router = APIRouter(prefix="/tax", tags=["tax"])


class W9UpdateRequest(BaseModel):
    tax_classification: str | None = Field(default=None, max_length=50)
    is_1099_eligible: bool | None = None
    w9_received_date: date | None = None
    tax_id: str | None = Field(default=None, max_length=50)


class TINVerifyRequest(BaseModel):
    # Optional override — when omitted we validate the TIN already on the
    # vendor row (``Vendor.tax_id``). A non-empty value updates the stored TIN
    # and validates the new one in the same call.
    tax_id: str | None = Field(default=None, max_length=50)


class FileBatchRequest(BaseModel):
    year: int = Field(..., ge=2000, le=2100)
    form_type: str = Field(default=FORM_NEC)
    # Idempotency anchor. When omitted we derive a deterministic key from
    # (org, year, form_type) so a naive retry without a key is still safe.
    idempotency_key: str | None = Field(default=None, max_length=120)


def _tax_settings(org: Organization) -> dict:
    return (org.settings or {}).get("tax") or {}


def _company_profile(org: Organization) -> dict:
    return (org.settings or {}).get("company") or {}


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


@router.get("/1099-report", status_code=status.HTTP_200_OK)
async def get_1099_report(
    year: int = Query(..., ge=2000, le=2100),
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    """Return the 1099 report for a calendar year.

    Aggregates ``completed`` payments by vendor for the given year. The
    response includes every vendor so the tenant can spot vendors they
    haven't yet flagged as 1099-eligible. The totals are labelled with the
    org's reporting (home) currency, and the aggregation only counts a
    payment it can PROVE is denominated in it — see
    ``services/tax_1099.build_1099_report``.
    """
    report = await build_1099_report(db, org_id, year, resolve_reporting_currency(org.settings))
    return report.to_dict()


@router.get("/1099-dashboard", status_code=status.HTTP_200_OK)
async def get_1099_dashboard(
    year: int = Query(..., ge=2000, le=2100),
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    """1099-eligible vendor compliance dashboard for a calendar year.

    Summarizes every 1099-eligible vendor with YTD totals, W-9-on-file +
    TIN-verified status, the $600 threshold flag, and a ``needs_attention``
    flag for over-threshold vendors missing a W-9 or TIN verification — i.e.
    the chase list before filing season.
    """
    dashboard = await build_1099_dashboard(
        db, org_id, year, resolve_reporting_currency(org.settings)
    )
    return dashboard.to_dict()


# ---------------------------------------------------------------------------
# Vendor W-9 management
# ---------------------------------------------------------------------------


@router.patch("/vendors/{vendor_id}/w9", status_code=status.HTTP_200_OK)
async def update_vendor_w9_fields(
    vendor_id: uuid.UUID,
    body: W9UpdateRequest,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    """Update W-9 / tax fields on a vendor without uploading a new file."""
    vendor = await _get_vendor_or_404(db, vendor_id)

    data = body.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(vendor, key, value)
    # PII-free: the FIELD NAMES that changed, never their values — `tax_id` is
    # one of them, and a TIN must never reach the audit trail (invariant #7).
    await dispatch_audit(
        db,
        correlation_id=uuid.uuid4(),
        organization_id=org_id,
        actor_id=user.id,
        action="vendor.tax_fields_updated",
        entity_type="vendor",
        entity_id=vendor.id,
        details={"changed_fields": sorted(data.keys())},
    )
    await db.commit()
    await db.refresh(vendor)
    return _vendor_tax_response(vendor)


@router.post("/vendors/{vendor_id}/w9", status_code=status.HTTP_200_OK)
async def upload_vendor_w9(
    vendor_id: uuid.UUID,
    file: UploadFile = File(...),
    tax_classification: str | None = Form(default=None),
    is_1099_eligible: bool = Form(default=True),
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    """Upload the vendor's signed W-9 PDF and mark them 1099-tracked."""
    vendor = await _get_vendor_or_404(db, vendor_id)

    content = await file.read()
    content_type = (file.content_type or "").lower()
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"File type '{content_type}' not allowed for W-9",
        )

    file_key = f"{org_id}/w9/{vendor.id}/{_safe_filename(file.filename)}"
    # boto3 is blocking — `_put_object` runs it in a worker thread.
    await _put_object(file_key, content, content_type)

    vendor.w9_file_key = file_key
    vendor.w9_received_date = utc_today()
    vendor.is_1099_eligible = is_1099_eligible
    if tax_classification:
        vendor.tax_classification = tax_classification
    # PII-free: no filename, no object key (the key embeds the uploader's
    # filename), no TIN — just that a W-9 landed and what it set.
    await dispatch_audit(
        db,
        correlation_id=uuid.uuid4(),
        organization_id=org_id,
        actor_id=user.id,
        action="vendor.w9_uploaded",
        entity_type="vendor",
        entity_id=vendor.id,
        details={
            "received_date": vendor.w9_received_date.isoformat(),
            "is_1099_eligible": vendor.is_1099_eligible,
            "tax_classification": vendor.tax_classification,
        },
    )
    await db.commit()
    await db.refresh(vendor)
    return _vendor_tax_response(vendor)


# ---------------------------------------------------------------------------
# TIN validation
# ---------------------------------------------------------------------------


@router.post("/vendors/{vendor_id}/tin-verify", status_code=status.HTTP_200_OK)
async def verify_vendor_tin(
    vendor_id: uuid.UUID,
    body: TINVerifyRequest,
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    """Validate a vendor's TIN and, on success, stamp ``tin_verified_at``.

    Runs through the configured TIN-validation adapter (``mock`` offline
    format/checksum by default; ``tax1099`` IRS TIN-match when configured).
    The response carries only the verdict + the redacted last-4 — never the
    TIN itself, so a TIN can't leak into a client body or a log line.
    """
    vendor = await _get_vendor_or_404(db, vendor_id)

    # An explicit tax_id in the request updates the stored TIN and validates
    # the new value; otherwise validate whatever is already on the row.
    if body.tax_id is not None:
        vendor.tax_id = body.tax_id
    tin = vendor.tax_id
    if not tin:
        raise HTTPException(status_code=400, detail="Vendor has no TIN on file")

    adapter = get_tin_validation_adapter(_tin_validation_config(org))
    result = await adapter.validate(
        tin=tin,
        legal_name=vendor.name,
        tin_type_hint=_tin_type_hint(vendor.tax_classification),
    )

    if result.is_valid:
        vendor.tin_verified_at = datetime.now(UTC)
    else:
        # A failed/indeterminate re-check clears any prior verification so the
        # dashboard never shows a stale green check against a bad TIN.
        vendor.tin_verified_at = None
    # PII-free: verdict + provider only. NEVER the TIN, and not the redacted
    # last-4 either — a durable trail is the wrong place for even a fragment.
    await dispatch_audit(
        db,
        correlation_id=uuid.uuid4(),
        organization_id=org_id,
        actor_id=user.id,
        action="vendor.tin_verified",
        entity_type="vendor",
        entity_id=vendor.id,
        details={
            "is_valid": result.is_valid,
            "provider": adapter.provider_name,
            "tax_id_replaced": body.tax_id is not None,
        },
    )
    await db.commit()
    await db.refresh(vendor)

    return {
        **_vendor_tax_response(vendor),
        "tin_validation": result.to_dict(),
    }


# ---------------------------------------------------------------------------
# 1099 form generation (PDF)
# ---------------------------------------------------------------------------


@router.get("/vendors/{vendor_id}/1099", status_code=status.HTTP_200_OK)
async def download_vendor_1099(
    vendor_id: uuid.UUID,
    year: int = Query(..., ge=2000, le=2100),
    form_type: str = Query(default=FORM_NEC),
    misc_box: str = Query(default="3"),
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    """Generate + download a vendor's 1099-NEC / 1099-MISC working copy PDF.

    The amount is the vendor's reportable YTD total for the year (from the
    1099 aggregation). Returns 400 if the form type is unsupported or the
    vendor has no reportable payments for the year.
    """
    if form_type not in {FORM_NEC, FORM_MISC}:
        raise HTTPException(status_code=400, detail="Unsupported form type")

    vendor = await _get_vendor_or_404(db, vendor_id)
    # The org's reporting currency, not the `USD` default: the aggregation
    # uses it to decide which payments it can express, so passing nothing
    # would drop a non-USD tenant's whole book out of the form amount.
    report = await build_1099_report(db, org_id, year, resolve_reporting_currency(org.settings))
    row = next((r for r in report.rows if r.vendor_id == vendor.id), None)
    if row is None or row.ytd_paid <= 0:
        raise HTTPException(
            status_code=400,
            detail="Vendor has no reportable payments for the requested year",
        )

    company = _company_profile(org)
    ctx = build_form_context(
        row=row,
        full_tax_id=vendor.tax_id,
        tax_year=year,
        form_type=form_type,
        payer_name=company.get("name") or org.name,
        payer_tax_id=company.get("tax_id"),
        payer_address=company.get("address"),
        recipient_address=vendor.address,
        misc_box=misc_box,
        brand=get_brand_context(org.settings),
    )
    pdf = await asyncio.to_thread(render_1099_pdf, ctx)
    filename = f"{form_type}-{year}-{vendor.id}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# 1099 e-filing
# ---------------------------------------------------------------------------


@router.post("/1099/file", status_code=status.HTTP_200_OK)
async def file_1099_batch(
    body: FileBatchRequest,
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    """Submit a year's 1099 forms for e-filing via the configured adapter.

    Idempotent: keyed on ``(organization_id, idempotency_key)`` — a retried
    submit with the same key returns the previously stored confirmation
    instead of re-filing (filing a duplicate with the IRS is a real
    problem). Only 1099-eligible vendors over the $600 threshold are filed.

    Claims the idempotency slot BEFORE calling the partner: a placeholder
    ``pending`` row is inserted and flushed first, so a concurrent duplicate
    submit (same key) hits the unique constraint immediately and never
    reaches the partner a second time — mirroring the payment-run "claim the
    slot, then act" pattern (``api/payments.py::execute_payment_run``) and the
    PEPPOL inbound claim-before-upload ordering (``services/peppol_receive.py``).
    """
    if body.form_type not in {FORM_NEC, FORM_MISC}:
        raise HTTPException(status_code=400, detail="Unsupported form type")

    idempotency_key = body.idempotency_key or f"{org_id}:{body.year}:{body.form_type}"

    # Fast-path (advisory): if we already filed this key, return the stored
    # result without calling the partner again. The flush below is the
    # authoritative guarantee for the concurrent race this check can miss.
    existing = await db.execute(
        select(Tax1099Filing).where(
            Tax1099Filing.organization_id == org_id,
            Tax1099Filing.idempotency_key == idempotency_key,
        )
    )
    prior = existing.scalar_one_or_none()
    if prior is not None:
        return _filing_response(prior, already_filed=True)

    adapter = get_tax_filing_adapter(_filing_config(org))

    # Claim the idempotency slot now, before any partner call. status="pending"
    # is a transient marker overwritten with the real outcome once the
    # partner responds (or the row is deleted below if the partner call
    # fails, so a legitimate retry isn't permanently blocked).
    filing = Tax1099Filing(
        organization_id=org_id,
        tax_year=body.year,
        provider=adapter.provider_name,
        idempotency_key=idempotency_key,
        status="pending",
        submitted_by=user.id,
    )
    db.add(filing)
    try:
        await db.flush()
    except IntegrityError:
        # Lost the race to a concurrent identical submit — the winner already
        # claimed (or has since completed) this idempotency key. Roll back our
        # half-inserted row; never reach the partner a second time.
        await db.rollback()
        winner = (
            await db.execute(
                select(Tax1099Filing).where(
                    Tax1099Filing.organization_id == org_id,
                    Tax1099Filing.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()
        if winner is not None:
            return _filing_response(winner, already_filed=True)
        # The racer's own claim vanished (its submit failed and released the
        # slot) between our IntegrityError and this re-read. Surface a clean
        # 409 rather than silently retrying into another race.
        raise HTTPException(
            status_code=409,
            detail="A concurrent 1099 filing submission is in progress for this key",
        ) from None

    # Reporting currency as above — the filed box amount must come from the
    # same aggregation the report endpoint shows.
    report = await build_1099_report(
        db, org_id, body.year, resolve_reporting_currency(org.settings)
    )
    filable = [r for r in report.rows if r.is_1099_eligible and r.over_threshold]

    forms = [
        FilingFormPayload(
            vendor_id=str(r.vendor_id),
            form_type=body.form_type,
            recipient_name=r.vendor_name,
            recipient_tin=r.tax_id or "",
            box_amount=r.ytd_paid,
            tax_year=body.year,
        )
        for r in filable
    ]

    try:
        result = await adapter.submit_batch(
            tax_year=body.year,
            forms=forms,
            idempotency_key=idempotency_key,
        )
    except Exception:
        # The partner call failed after we claimed the slot. Release the
        # reservation so a legitimate retry (same idempotency key) isn't
        # permanently blocked by our own placeholder row.
        await db.delete(filing)
        await db.commit()
        raise

    filing.status = result.status
    filing.confirmation_number = result.confirmation_number
    filing.submitted_count = result.submitted_count
    filing.accepted_count = result.accepted_count
    filing.rejected_count = result.rejected_count
    filing.result = {"forms": result.to_dict()["forms"]}
    # Filing with the IRS is a regulated, externally-visible act — the trail
    # records who submitted what and the partner's verdict. PII-free: counts
    # and the confirmation number, never a recipient name, TIN or box amount.
    await dispatch_audit(
        db,
        correlation_id=uuid.uuid4(),
        organization_id=org_id,
        actor_id=user.id,
        action="tax_1099.filed",
        entity_type="tax_1099_filing",
        entity_id=filing.id,
        details={
            "tax_year": filing.tax_year,
            "form_type": body.form_type,
            "provider": filing.provider,
            "status": filing.status,
            "confirmation_number": filing.confirmation_number,
            "submitted_count": filing.submitted_count,
            "accepted_count": filing.accepted_count,
            "rejected_count": filing.rejected_count,
        },
    )
    await db.commit()
    await db.refresh(filing)
    return _filing_response(filing, already_filed=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tin_validation_config(org: Organization) -> dict:
    """Per-org TIN-validation config; falls back to the process-level default
    provider so a tenant without explicit config still validates offline."""
    cfg = dict(_tax_settings(org).get("tin_validation") or {})
    cfg.setdefault("provider", app_settings.tin_validation_provider)
    return cfg


def _filing_config(org: Organization) -> dict:
    cfg = dict(_tax_settings(org).get("filing") or {})
    cfg.setdefault("provider", app_settings.tax_filing_provider)
    return cfg


def _tin_type_hint(tax_classification: str | None) -> str | None:
    """Map the W-9 entity classification to an EIN/SSN hint. Individuals /
    sole proprietors typically use an SSN; everything else an EIN."""
    if not tax_classification:
        return None
    indiv = {"individual", "sole_proprietor"}
    return "ssn" if tax_classification.lower() in indiv else "ein"


def _filing_response(filing: Tax1099Filing, *, already_filed: bool) -> dict:
    return {
        "filing_id": str(filing.id),
        "year": filing.tax_year,
        "provider": filing.provider,
        "status": filing.status,
        "confirmation_number": filing.confirmation_number,
        "submitted_count": filing.submitted_count,
        "accepted_count": filing.accepted_count,
        "rejected_count": filing.rejected_count,
        "already_filed": already_filed,
        "forms": (filing.result or {}).get("forms", []),
    }


async def _get_vendor_or_404(db: AsyncSession, vendor_id: uuid.UUID) -> Vendor:
    q = await db.execute(select(Vendor).where(Vendor.id == vendor_id))
    vendor = q.scalar_one_or_none()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")
    return vendor


def _vendor_tax_response(vendor: Vendor) -> dict:
    return {
        "vendor_id": str(vendor.id),
        "tax_id": vendor.tax_id,
        "tax_classification": vendor.tax_classification,
        "is_1099_eligible": vendor.is_1099_eligible,
        "w9_received_date": (
            vendor.w9_received_date.isoformat() if vendor.w9_received_date else None
        ),
        "w9_on_file": vendor.w9_file_key is not None,
        "tin_verified_at": (
            vendor.tin_verified_at.isoformat()
            if isinstance(vendor.tin_verified_at, datetime)
            else None
        ),
    }
