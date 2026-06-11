"""CRUD endpoints for workflow definitions."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy import update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ROLE_ADMIN, get_current_user, get_org_id, require_roles
from app.api.pagination import PaginationParams, pagination_params
from app.models.user import User
from app.models.workflow import WorkflowDefinition, WorkflowInstance
from app.schemas.workflow import (
    WorkflowDefinitionCreate,
    WorkflowDefinitionListResponse,
    WorkflowDefinitionResponse,
    WorkflowDefinitionUpdate,
)
from app.services.workflow_engine import DEFAULT_STEPS_CONFIG
from app.tenant import get_tenant_db

router = APIRouter(prefix="/workflows", tags=["workflows"])


@router.get("/active/steps")
async def get_active_steps(
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_org_id),
):
    """Return which steps are enabled in the active workflow."""
    from app.services.workflow_engine import get_or_create_workflow_definition

    defn = await get_or_create_workflow_definition(db, org_id)
    steps = defn.steps_config.get("steps", [])
    result: dict = {}
    for step in steps:
        step_type = step["type"]
        result[step_type] = step.get("enabled", True)
        if step_type == "approval" and step.get("enabled", True):
            cfg = step.get("config", {})
            result["approval_config"] = {
                "approver_strategy": cfg.get("approver_strategy", "manual"),
                "approver_ids": cfg.get("approver_ids", []),
            }
    return result


@router.get("", response_model=WorkflowDefinitionListResponse)
async def list_workflows(
    pagination: PaginationParams = Depends(pagination_params),
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_org_id),
):
    base = select(WorkflowDefinition).where(WorkflowDefinition.organization_id == org_id)
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar() or 0

    # Auto-create the default workflow if the org has none at all — independent
    # of which page was requested, so a stray `?page=2` can't trigger a second
    # default. After creation the first page holds exactly that row.
    if total == 0:
        default = WorkflowDefinition(
            name="Default Workflow",
            description="Standard invoice processing: extract, review, and send to ERP.",
            steps_config=DEFAULT_STEPS_CONFIG,
            is_active=True,
            is_default=True,
            organization_id=org_id,
        )
        db.add(default)
        await db.flush()
        await db.refresh(default)
        total = 1

    result = await db.execute(
        base.order_by(WorkflowDefinition.is_default.desc(), WorkflowDefinition.created_at)
        .offset(pagination.offset)
        .limit(pagination.limit)
    )
    definitions = result.scalars().all()

    return WorkflowDefinitionListResponse(
        items=[WorkflowDefinitionResponse.from_db(d) for d in definitions],
        total=int(total),
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.post("", response_model=WorkflowDefinitionResponse, status_code=status.HTTP_201_CREATED)
async def create_workflow(
    body: WorkflowDefinitionCreate,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    steps_config = {"steps": [s.model_dump() for s in body.steps]}
    # New workflows start inactive — user must explicitly activate
    defn = WorkflowDefinition(
        name=body.name,
        description=body.description,
        steps_config=steps_config,
        is_active=False,
        is_default=False,
        organization_id=org_id,
    )
    db.add(defn)
    await db.flush()
    await db.refresh(defn)
    return WorkflowDefinitionResponse.from_db(defn)


@router.get("/{workflow_id}", response_model=WorkflowDefinitionResponse)
async def get_workflow(
    workflow_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_org_id),
):
    result = await db.execute(
        select(WorkflowDefinition).where(
            WorkflowDefinition.id == workflow_id,
            WorkflowDefinition.organization_id == org_id,
        )
    )
    defn = result.scalar_one_or_none()
    if not defn:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return WorkflowDefinitionResponse.from_db(defn)


@router.patch("/{workflow_id}", response_model=WorkflowDefinitionResponse)
async def update_workflow(
    workflow_id: uuid.UUID,
    body: WorkflowDefinitionUpdate,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    result = await db.execute(
        select(WorkflowDefinition).where(
            WorkflowDefinition.id == workflow_id,
            WorkflowDefinition.organization_id == org_id,
        )
    )
    defn = result.scalar_one_or_none()
    if not defn:
        raise HTTPException(status_code=404, detail="Workflow not found")

    if body.name is not None:
        defn.name = body.name
    if body.description is not None:
        defn.description = body.description
    if body.is_active is not None:
        if body.is_active and not defn.is_active:
            # Enforce the one-active-workflow invariant: when this
            # workflow flips inactive → active, deactivate any peer
            # that's currently active in this org.
            await db.execute(
                sql_update(WorkflowDefinition)
                .where(
                    WorkflowDefinition.organization_id == org_id,
                    WorkflowDefinition.id != workflow_id,
                )
                .values(is_active=False)
            )
        defn.is_active = body.is_active
    if body.steps is not None:
        defn.steps_config = {"steps": [s.model_dump() for s in body.steps]}

    await db.flush()
    await db.refresh(defn)
    return WorkflowDefinitionResponse.from_db(defn)


async def _workflow_instance_count(db: AsyncSession, workflow_id: uuid.UUID) -> int:
    """Count workflow_instances bound to this definition.

    Each instance freezes a steps_config snapshot at invoice creation,
    so deleting the definition out from under live instances would
    leave them with a dangling FK and confuse the editor history.
    """
    result = await db.execute(
        select(func.count())
        .select_from(WorkflowInstance)
        .where(WorkflowInstance.definition_id == workflow_id)
    )
    return int(result.scalar() or 0)


@router.delete("/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workflow(
    workflow_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    result = await db.execute(
        select(WorkflowDefinition).where(
            WorkflowDefinition.id == workflow_id,
            WorkflowDefinition.organization_id == org_id,
        )
    )
    defn = result.scalar_one_or_none()
    if not defn:
        raise HTTPException(status_code=404, detail="Workflow not found")
    if defn.is_default:
        raise HTTPException(status_code=409, detail="Cannot delete the default workflow")
    if defn.is_active:
        raise HTTPException(
            status_code=409,
            detail="Cannot delete an active workflow — deactivate it first",
        )

    instance_count = await _workflow_instance_count(db, workflow_id)
    if instance_count:
        raise HTTPException(
            status_code=409,
            detail={
                "message": (
                    "Cannot delete workflow — it is the snapshot source for "
                    f"{instance_count} in-flight invoice"
                    f"{'s' if instance_count != 1 else ''}."
                ),
                "instance_count": instance_count,
            },
        )

    await db.delete(defn)


class BulkDeleteWorkflowRequest(BaseModel):
    workflow_ids: list[str]


class BulkDeleteWorkflowFailure(BaseModel):
    workflow_id: str
    reason: str  # "not_found" | "default" | "active" | "instances"
    instance_count: int | None = None


class BulkDeleteWorkflowResponse(BaseModel):
    deleted: list[str]
    failed: list[BulkDeleteWorkflowFailure]


@router.post("/bulk-delete", response_model=BulkDeleteWorkflowResponse)
async def bulk_delete_workflows(
    body: BulkDeleteWorkflowRequest,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    """Best-effort delete of multiple workflows.

    Each id is processed independently. The same three guards apply
    per-id as the single-delete endpoint: default / active / has
    instances.
    """
    deleted: list[str] = []
    failed: list[BulkDeleteWorkflowFailure] = []

    for raw_id in body.workflow_ids:
        try:
            wf_uuid = uuid.UUID(raw_id)
        except ValueError:
            failed.append(BulkDeleteWorkflowFailure(workflow_id=raw_id, reason="not_found"))
            continue

        result = await db.execute(
            select(WorkflowDefinition).where(
                WorkflowDefinition.id == wf_uuid,
                WorkflowDefinition.organization_id == org_id,
            )
        )
        defn = result.scalar_one_or_none()
        if not defn:
            failed.append(BulkDeleteWorkflowFailure(workflow_id=raw_id, reason="not_found"))
            continue
        if defn.is_default:
            failed.append(BulkDeleteWorkflowFailure(workflow_id=raw_id, reason="default"))
            continue
        if defn.is_active:
            failed.append(BulkDeleteWorkflowFailure(workflow_id=raw_id, reason="active"))
            continue

        instance_count = await _workflow_instance_count(db, wf_uuid)
        if instance_count:
            failed.append(
                BulkDeleteWorkflowFailure(
                    workflow_id=raw_id,
                    reason="instances",
                    instance_count=instance_count,
                )
            )
            continue

        await db.delete(defn)
        deleted.append(raw_id)

    return BulkDeleteWorkflowResponse(deleted=deleted, failed=failed)
