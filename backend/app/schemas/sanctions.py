"""Response schemas for sanctions screening + vendor risk.

Single-authored shared contract so the screening endpoints (api/vendors.py)
and the risk endpoints (api/vendor_risk.py) agree on shapes. None of these
expose raw provider match details — only the list NAME, the verdict, scores,
and timestamps (invariant #7).
"""

from __future__ import annotations

from pydantic import BaseModel


class SanctionsCheckResponse(BaseModel):
    """One row of a vendor's screening trail (`sanctions_checks`)."""

    id: str
    vendor_id: str
    provider: str
    check_type: str  # initial | periodic | manual | pre_payment
    result: str  # clear | match | review_required
    risk_score: str | None = None
    matched_list: str | None = None
    checked_at: str

    @classmethod
    def from_db(cls, c) -> SanctionsCheckResponse:
        return cls(
            id=str(c.id),
            vendor_id=str(c.vendor_id),
            provider=c.provider,
            check_type=c.check_type,
            result=c.result,
            risk_score=str(c.risk_score) if c.risk_score is not None else None,
            matched_list=c.matched_list,
            checked_at=c.checked_at.isoformat() if c.checked_at else "",
        )


class ScreeningReviewItem(BaseModel):
    """A vendor needing screening attention (match / review_required), for the
    review-queue surface."""

    vendor_id: str
    vendor_name: str
    screening_status: str
    last_screened_at: str | None = None
    payments_blocked: bool = False
    risk_level: str = "unknown"
    risk_score: str | None = None
    latest_matched_list: str | None = None
    latest_provider: str | None = None


class VendorBlockRequest(BaseModel):
    """Manual block / unblock payload."""

    reason: str | None = None


class VendorRiskResponse(BaseModel):
    """Composite risk for one vendor."""

    vendor_id: str
    risk_score: str | None = None
    risk_level: str = "unknown"
    risk_factors: dict | None = None
    risk_scored_at: str | None = None


class VendorRiskSummaryItem(BaseModel):
    """One bucket of the org-wide risk distribution."""

    risk_level: str
    count: int
