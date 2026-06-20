"""Pydantic v2 schemas for the adaptive-workflow endpoints.

Money / statistic Decimals serialise as **strings** (matching the project's
string-Decimal convention for adaptive stats and the JSON examples in
``backend/docs/adaptive-workflows.md``) — the API layer stringifies the
``Decimal`` values from the pure-stat service before constructing these models.
Nothing here is a wire ``float``.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Approval patterns
# ---------------------------------------------------------------------------


class ApproverPatternResponse(BaseModel):
    approver_id: str
    approver_name: str | None = None
    approved_count: int
    rejected_count: int
    approval_rate_pct: str
    median_time_to_approve_days: str
    avg_time_to_approve_days: str
    sample_size: int


class VendorPatternResponse(BaseModel):
    vendor_id: str | None = None
    vendor_name: str
    approved_count: int
    rejected_count: int
    approval_rate_pct: str
    unmodified_count: int
    consistency_pct: str
    avg_approved_amount: str
    median_approved_amount: str
    min_approved_amount: str
    max_approved_amount: str
    sample_size: int


class ApprovalPatternsResponse(BaseModel):
    generated_at: str
    lookback_days: int
    entity_id: str | None = None
    approvers: list[ApproverPatternResponse]
    vendors: list[VendorPatternResponse]


# ---------------------------------------------------------------------------
# Anomalies
# ---------------------------------------------------------------------------


class VendorBaselineResponse(BaseModel):
    vendor_id: str | None = None
    vendor_name: str
    sample_size: int
    mean_amount: str
    median_amount: str
    stdev_amount: str
    min_amount: str
    max_amount: str
    typical_approver_ids: list[str]
    median_time_to_approve_days: str


class AnomalyFlagResponse(BaseModel):
    code: str
    severity: str
    message: str
    observed: str
    expected: str


class InvoiceAnomalyResponse(BaseModel):
    invoice_id: str
    vendor_id: str | None = None
    vendor_name: str
    amount: str
    insufficient_history: bool
    baseline: VendorBaselineResponse | None = None
    flags: list[AnomalyFlagResponse]


class AnomalyBatchResponse(BaseModel):
    total_scanned: int
    flagged: list[InvoiceAnomalyResponse]


# ---------------------------------------------------------------------------
# Suggestions
# ---------------------------------------------------------------------------


class SuggestionResponse(BaseModel):
    id: uuid.UUID
    kind: str
    vendor_id: str | None = None
    vendor_name: str
    title: str
    rationale: str | None = None
    payload: dict
    confidence_pct: str
    status: str
    created_at: str | None = None
    dismissed_at: str | None = None


class SuggestionListResponse(BaseModel):
    suggestions: list[SuggestionResponse]


# ---------------------------------------------------------------------------
# Smart routing (advisory)
# ---------------------------------------------------------------------------


class RoutingCandidateResponse(BaseModel):
    approver_id: str
    approver_name: str | None = None
    score: str  # 0-100, string-Decimal
    rank: int
    median_time_to_approve_days: str
    approval_rate_pct: str
    sample_size: int
    vendor_approved_count: int
    reasons: list[str]


class RoutingSuggestionResponse(BaseModel):
    invoice_id: str | None = None
    vendor_id: str | None = None
    vendor_name: str
    amount: str
    insufficient_history: bool
    candidates: list[RoutingCandidateResponse]


class ApplyRoutingRequest(BaseModel):
    """Body for the smart-routing apply path — assign the top-ranked eligible
    approver to one invoice via the audited assignment service."""

    invoice_id: uuid.UUID


class ApplyRoutingResponse(BaseModel):
    """Outcome of applying the routing recommendation.

    ``assigned`` is False on the idempotent no-op (the invoice was already
    assigned to the chosen approver — no second audit row is written)."""

    invoice_id: str
    assigned: bool
    assigned_to_id: str
    assigned_to_name: str | None = None
    rank: int  # the chosen candidate's rank (always 1 — the top recommendation)
    score: str  # the chosen candidate's routing score (string-Decimal)


__all__ = [
    "ApproverPatternResponse",
    "VendorPatternResponse",
    "ApprovalPatternsResponse",
    "VendorBaselineResponse",
    "AnomalyFlagResponse",
    "InvoiceAnomalyResponse",
    "AnomalyBatchResponse",
    "SuggestionResponse",
    "SuggestionListResponse",
    "RoutingCandidateResponse",
    "RoutingSuggestionResponse",
    "ApplyRoutingRequest",
    "ApplyRoutingResponse",
]
