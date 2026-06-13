"""Pydantic v2 schemas for the data-enrichment endpoints.

Money / statistic Decimals serialise as **strings** (matching the project's
string-Decimal convention — never a wire ``float``). The API layer stringifies
the ``Decimal`` values from the pure-stat service before constructing these
models. N/A scores are typed ``str | None``.
"""

from __future__ import annotations

from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Suggestions (auto-fill + price variance)
# ---------------------------------------------------------------------------


class FieldSuggestionOut(BaseModel):
    field: str
    value: str
    confidence: str
    sample_size: int
    occurrences: int
    evidence: str
    runner_up: str | None = None


class PriceVarianceOut(BaseModel):
    line_index: int
    item_key: str
    description: str | None = None
    current_unit_price: str
    baseline_unit_price: str
    delta: str
    delta_pct: str
    sample_size: int
    direction: str
    severity: str


class EnrichmentSuggestionsResponse(BaseModel):
    invoice_id: str
    vendor_id: str | None = None
    field_suggestions: list[FieldSuggestionOut]
    price_variances: list[PriceVarianceOut]
    generated_at: str


# ---------------------------------------------------------------------------
# Vendor score
# ---------------------------------------------------------------------------


class SubScoreOut(BaseModel):
    name: str
    score: str | None = None
    sample_size: int
    detail: str


class VendorScoreResponse(BaseModel):
    vendor_id: str
    vendor_name: str
    composite: str | None = None
    sub_scores: list[SubScoreOut]
    computed_at: str


__all__ = [
    "FieldSuggestionOut",
    "PriceVarianceOut",
    "EnrichmentSuggestionsResponse",
    "SubScoreOut",
    "VendorScoreResponse",
]
