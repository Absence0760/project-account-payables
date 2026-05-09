"""Pydantic schemas for workflow action endpoints."""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field

# ---------- workflow definition schemas ----------


class ExtractionStepConfig(BaseModel):
    auto_approve_enabled: bool = False
    auto_approve_threshold: float = Field(default=0.95, ge=0.0, le=1.0)


class ApprovalLevelConfig(BaseModel):
    """One level in a multi-level approval chain."""

    min_amount: float | None = None
    max_amount: float | None = None
    approver_ids: list[str] = []
    required_approvals: int = 1
    name: str = ""


class ApprovalStepConfig(BaseModel):
    required: bool = True
    approver_id: str | None = None  # deprecated, use approver_ids
    approver_ids: list[str] = []
    approver_strategy: str = "manual"  # "manual", "specific", "auto", "chain"
    auto_approve_below: float | None = None  # auto-approve invoices below this amount
    require_cfo_above: float | None = None  # require CFO approval above this amount
    max_invoice_amount: float | None = None  # reject invoices above this amount
    approval_chain: list[ApprovalLevelConfig] = []  # used when strategy="chain"
    require_segregation: bool = True  # approver ≠ uploader (classic AP invariant; SOC 2 baseline)


class ErpExportStepConfig(BaseModel):
    export_format: str = "json"  # "json", "xml", "csv", "cxml", "edi"
    auto_send_on_approval: bool = True  # send to ERP immediately after approval
    include_line_items: bool = True  # include line item details in the payload
    include_attachments: bool = False  # include PDF file URL in the payload


class WorkflowStepConfig(BaseModel):
    number: int
    type: str  # "extraction", "approval", "erp_export"
    name: str
    enabled: bool = True
    config: ExtractionStepConfig | ApprovalStepConfig | ErpExportStepConfig | dict = Field(
        default_factory=dict
    )


class WorkflowDefinitionCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    steps: list[WorkflowStepConfig]


class WorkflowDefinitionUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    description: str | None = None
    is_active: bool | None = None
    steps: list[WorkflowStepConfig] | None = None


class WorkflowDefinitionResponse(BaseModel):
    id: str
    name: str
    description: str | None
    steps_config: dict
    is_active: bool
    is_default: bool
    created_at: str
    updated_at: str | None

    @classmethod
    def from_db(cls, defn) -> "WorkflowDefinitionResponse":
        return cls(
            id=str(defn.id),
            name=defn.name,
            description=defn.description,
            steps_config=defn.steps_config,
            is_active=defn.is_active,
            is_default=defn.is_default,
            created_at=defn.created_at.isoformat() if defn.created_at else "",
            updated_at=defn.updated_at.isoformat() if defn.updated_at else None,
        )


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
    actor_name: str | None = None
    action: str
    entity_type: str
    entity_id: str | None
    details: dict | None
    created_at: str

    @classmethod
    def from_db(cls, entry, actor_names: dict[str, str] | None = None) -> "AuditLogEntryResponse":
        actor_id_str = str(entry.actor_id) if entry.actor_id else None
        return cls(
            id=str(entry.id),
            correlation_id=str(entry.correlation_id) if entry.correlation_id else "",
            actor_id=actor_id_str,
            actor_name=(actor_names or {}).get(actor_id_str, None) if actor_id_str else None,
            action=entry.action,
            entity_type=entry.entity_type,
            entity_id=str(entry.entity_id) if entry.entity_id else None,
            details=entry.details,
            created_at=entry.created_at.isoformat() if entry.created_at else "",
        )
