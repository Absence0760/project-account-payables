"""Pydantic v2 schemas for the workflow A/B-testing (experiments) endpoints.

Statistic Decimals serialise as **strings** (matching the adaptive-workflow
convention) — the API layer stringifies the ``Decimal`` values from the pure
``services/workflow_experiments`` metrics before constructing these models.
Nothing here is a wire ``float``.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class ExperimentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    workflow_definition_id: uuid.UUID
    config_a: dict
    config_b: dict
    split_a_pct: int = Field(default=50, ge=0, le=100)
    primary_metric: str = "time_to_approval_days"
    min_sample_per_variant: int = Field(default=10, ge=1, le=10000)


class ExperimentUpdate(BaseModel):
    """Partial update — only allowed while the experiment is ``draft``."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    config_a: dict | None = None
    config_b: dict | None = None
    split_a_pct: int | None = Field(default=None, ge=0, le=100)
    primary_metric: str | None = None
    min_sample_per_variant: int | None = Field(default=None, ge=1, le=10000)


class ExperimentResponse(BaseModel):
    id: str
    name: str
    description: str | None = None
    workflow_definition_id: str
    workflow_definition_name: str | None = None
    config_a: dict
    config_b: dict
    split_a_pct: int
    primary_metric: str
    min_sample_per_variant: int
    status: str
    started_at: str | None = None
    ended_at: str | None = None
    assigned_count: int
    entity_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class ExperimentListResponse(BaseModel):
    experiments: list[ExperimentResponse]


class VariantMetricsResponse(BaseModel):
    variant: str
    assigned_count: int
    completed_count: int
    approved_count: int
    rejected_count: int
    touchless_count: int
    exception_count: int
    median_time_to_approval_days: str
    avg_time_to_approval_days: str
    touchless_rate_pct: str
    exception_rate_pct: str
    rejection_rate_pct: str


class ExperimentResultsResponse(BaseModel):
    experiment_id: str
    experiment_name: str
    status: str
    primary_metric: str
    min_sample_per_variant: int
    enough_data: bool
    winner: str | None = None
    rationale: str
    notes: list[str]
    variant_a: VariantMetricsResponse
    variant_b: VariantMetricsResponse
    generated_at: str
