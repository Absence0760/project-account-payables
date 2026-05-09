"""CRUD endpoints for workflow definitions."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ROLE_ADMIN, get_current_user, get_org_id, require_roles
from app.models.user import User
from app.models.workflow import WorkflowDefinition
from app.schemas.workflow import (
    WorkflowDefinitionCreate,
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


@router.get("", response_model=list[WorkflowDefinitionResponse])
async def list_workflows(
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(get_current_user),
    org_id: uuid.UUID = Depends(get_org_id),
):
    result = await db.execute(
        select(WorkflowDefinition)
        .where(WorkflowDefinition.organization_id == org_id)
        .order_by(WorkflowDefinition.is_default.desc(), WorkflowDefinition.created_at)
    )
    definitions = result.scalars().all()

    # Auto-create default workflow if none exist
    if not definitions:
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
        definitions = [default]

    return [WorkflowDefinitionResponse.from_db(d) for d in definitions]


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
    await db.delete(defn)
