"""Tax endpoints — currently 1099 reporting + W-9 upload.

All endpoints are ``admin`` + ``ap_manager`` only. CFO can read but not
upload W-9s (upload is a data-ingest action, not a finance review one).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    ROLE_ADMIN,
    ROLE_AP_MANAGER,
    ROLE_CFO,
    get_org_id,
    require_roles,
)
from app.config import settings as app_settings
from app.models.user import User
from app.models.vendor import Vendor
from app.services.storage import ALLOWED_CONTENT_TYPES, _ensure_bucket, _get_client
from app.services.tax_1099 import build_1099_report
from app.tenant import get_tenant_db

router = APIRouter(prefix="/tax", tags=["tax"])


class W9UpdateRequest(BaseModel):
    tax_classification: str | None = Field(default=None, max_length=50)
    is_1099_eligible: bool | None = None
    w9_received_date: date | None = None
    tax_id: str | None = Field(default=None, max_length=50)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


@router.get("/1099-report", status_code=status.HTTP_200_OK)
async def get_1099_report(
    year: int = Query(..., ge=2000, le=2100),
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    """Return the 1099 report for a calendar year.

    Aggregates ``completed`` payments by vendor for the given year. The
    response includes every vendor so the tenant can spot vendors they
    haven't yet flagged as 1099-eligible.
    """
    report = await build_1099_report(db, org_id, year)
    return report.to_dict()


# ---------------------------------------------------------------------------
# Vendor W-9 management
# ---------------------------------------------------------------------------


@router.patch("/vendors/{vendor_id}/w9", status_code=status.HTTP_200_OK)
async def update_vendor_w9_fields(
    vendor_id: uuid.UUID,
    body: W9UpdateRequest,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER)),
):
    """Update W-9 / tax fields on a vendor without uploading a new file."""
    vendor = await _get_vendor_or_404(db, vendor_id)

    data = body.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(vendor, key, value)
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

    file_key = f"{org_id}/w9/{vendor.id}/{file.filename}"
    s3 = _get_client()
    _ensure_bucket(s3)
    s3.put_object(
        Bucket=app_settings.s3_bucket,
        Key=file_key,
        Body=content,
        ContentType=content_type,
    )

    vendor.w9_file_key = file_key
    vendor.w9_received_date = date.today()
    vendor.is_1099_eligible = is_1099_eligible
    if tax_classification:
        vendor.tax_classification = tax_classification
    await db.commit()
    await db.refresh(vendor)
    return _vendor_tax_response(vendor)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
