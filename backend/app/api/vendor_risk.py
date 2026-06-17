"""Vendor risk endpoints — composite risk score + org-wide distribution.

NOTE: foundation stub. Full implementation (recompute, factor breakdown,
summary distribution) is done by the "risk scoring & adverse media" worker.
The `router` object and its mount in `main.py` are fixed — keep the prefix
and the `router` symbol stable.

Mounted under `/api/vendors` alongside the main vendors router.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    ROLE_ADMIN,
    ROLE_AP_CLERK,
    ROLE_AP_MANAGER,
    ROLE_CFO,
    require_roles,
)
from app.models.user import User
from app.models.vendor import Vendor
from app.schemas.sanctions import VendorRiskResponse
from app.tenant import get_tenant_db

router = APIRouter(prefix="/vendors", tags=["vendor-risk"])


@router.get("/{vendor_id}/risk", response_model=VendorRiskResponse)
async def get_vendor_risk(
    vendor_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_AP_CLERK, ROLE_CFO)),
):
    """Return the vendor's persisted composite risk (reads denormalised
    columns; recompute is a separate POST — implemented by the risk worker)."""
    vendor = (await db.execute(select(Vendor).where(Vendor.id == vendor_id))).scalar_one_or_none()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")
    return VendorRiskResponse(
        vendor_id=str(vendor.id),
        risk_score=str(vendor.risk_score) if vendor.risk_score is not None else None,
        risk_level=vendor.risk_level,
        risk_factors=vendor.risk_factors,
        risk_scored_at=vendor.risk_scored_at.isoformat() if vendor.risk_scored_at else None,
    )
