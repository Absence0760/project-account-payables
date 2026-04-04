"""Pydantic schemas for workflow action endpoints."""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field


# ---------- request schemas ----------


class AssignReviewerRequest(BaseModel):
    user_id: str


class ApproveRequest(BaseModel):
    """Optional field corrections applied at approval time."""
    vendor: str | None = Field(default=None, max_length=255)
    invoice_number: str | None = Field(default=None, max_length=100)
    amount: Decimal | None = Field(default=None, ge=0)
    description: str | None = None
    po_number: str | None = Field(default=None, max_length=100)
    due_date: date | None = None
    gl_account: str | None = Field(default=None, max_length=100)
    cost_center: str | None = Field(default=None, max_length=100)


class RejectRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=2000)


# ---------- response schemas ----------


class WorkflowStepResponse(BaseModel):
    id: str
    step_number: int
    step_type: str
    assigned_to: str | None
    action: str | None
    completed_at: str | None
    created_at: str

    @classmethod
    def from_db(cls, step) -> "WorkflowStepResponse":
        return cls(
            id=str(step.id),
            step_number=step.step_number,
            step_type=step.step_type,
            assigned_to=str(step.assigned_to) if step.assigned_to else None,
            action=step.action,
            completed_at=step.completed_at.isoformat() if step.completed_at else None,
            created_at=step.created_at.isoformat() if step.created_at else "",
        )


class WorkflowInstanceResponse(BaseModel):
    id: str
    correlation_id: str
    definition_id: str
    invoice_id: str
    current_step: int
    state: str
    state_data: dict | None
    steps: list[WorkflowStepResponse]
    created_at: str

    @classmethod
    def from_db(cls, instance, steps: list) -> "WorkflowInstanceResponse":
        return cls(
            id=str(instance.id),
            correlation_id=str(instance.correlation_id),
            definition_id=str(instance.definition_id),
            invoice_id=str(instance.invoice_id),
            current_step=instance.current_step,
            state=instance.state,
            state_data=instance.state_data,
            steps=[WorkflowStepResponse.from_db(s) for s in steps],
            created_at=instance.created_at.isoformat() if instance.created_at else "",
        )


class AuditLogEntryResponse(BaseModel):
    id: str
    correlation_id: str
    actor_id: str | None
    action: str
    entity_type: str
    entity_id: str | None
    details: dict | None
    created_at: str

    @classmethod
    def from_db(cls, entry) -> "AuditLogEntryResponse":
        return cls(
            id=str(entry.id),
            correlation_id=str(entry.correlation_id) if entry.correlation_id else "",
            actor_id=str(entry.actor_id) if entry.actor_id else None,
            action=entry.action,
            entity_type=entry.entity_type,
            entity_id=str(entry.entity_id) if entry.entity_id else None,
            details=entry.details,
            created_at=entry.created_at.isoformat() if entry.created_at else "",
        )
