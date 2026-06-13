"""Pydantic schemas for the autonomous exception-agent endpoints.

Mirrors the `AgentDecision` model's shape so the three `/exceptions/agent-*`
routes declare a typed `response_model` (project convention — model and schema
move together). `confidence` serialises as a float for the JSON response; it is
a 0–1 probability for display only and is stored exact (`Numeric(5,4)`) on the
row — never a monetary amount.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel

from app.api.pagination import PageMeta


class AgentDecisionResponse(BaseModel):
    id: uuid.UUID
    exception_id: uuid.UUID
    invoice_id: uuid.UUID
    exception_type: str
    action_taken: str
    confidence: float  # display only — stored exact as Numeric(5,4)
    rationale: str | None = None
    changes: dict | None = None
    autonomy_level: str
    agent_type: str
    created_at: str


class AgentDecisionListResponse(PageMeta):
    items: list[AgentDecisionResponse]
    total: int


class AgentStatsResponse(BaseModel):
    total_decisions: int
    auto_resolved: int
    escalated: int
    no_action: int
    resolution_rate: float
    escalation_rate: float
    # Accuracy needs a human-overturn signal — not tracked in this slice.
    accuracy: float | None = None


class AgentResolveExceptionRef(BaseModel):
    id: uuid.UUID
    status: str


class AgentResolveResponse(BaseModel):
    exception: AgentResolveExceptionRef
    decision: AgentDecisionResponse


__all__ = [
    "AgentDecisionResponse",
    "AgentDecisionListResponse",
    "AgentStatsResponse",
    "AgentResolveExceptionRef",
    "AgentResolveResponse",
]
