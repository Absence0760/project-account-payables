"""Conversational AP Assistant API.

All routes are under ``/api/assistant``, behind ``get_current_user``
(auth-before-everything) and tenant-scoped via ``get_tenant`` / ``get_tenant_db``.
Employee roles only (vendor-portal JWTs are already rejected by
``get_current_user``). History is scoped to ``(org, user)``: a user sees only
their own threads, and another user's / tenant's conversation id 404s (it does
not 403, so it can't enumerate).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    ROLE_ADMIN,
    ROLE_AP_CLERK,
    ROLE_AP_MANAGER,
    ROLE_CFO,
    require_roles,
)
from app.database import get_control_db
from app.models.assistant import Conversation, ConversationMessage
from app.models.organization import Organization
from app.models.user import User
from app.schemas.assistant import (
    ChatRequest,
    ChatResponse,
    ConversationDetail,
    ConversationListResponse,
    ConversationSummary,
    MessageOut,
    ToolInvocationOut,
    UsageDelta,
    UsageResponse,
)
from app.services.assistant import usage as usage_service
from app.services.assistant.orchestrator import run_turn
from app.services.assistant.usage import AssistantBudgetExceeded
from app.tenant import get_entity_id, get_tenant, get_tenant_db

router = APIRouter(prefix="/assistant", tags=["assistant"])

# Any authenticated employee role — the assistant only reads what the
# tenant-scoped tools expose.
_ASSISTANT_ROLES = (ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_AP_CLERK, ROLE_CFO)


def _invocations_out(tool_calls: dict) -> list[ToolInvocationOut]:
    return [
        ToolInvocationOut(
            tool=inv.get("tool", ""),
            args=inv.get("args", {}) or {},
            result=inv.get("result"),
            error=inv.get("error"),
        )
        for inv in (tool_calls or {}).get("invocations", [])
    ]


@router.post("/chat", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    tenant_db: AsyncSession = Depends(get_tenant_db),
    control_db: AsyncSession = Depends(get_control_db),
    org: Organization = Depends(get_tenant),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
    user: User = Depends(require_roles(*_ASSISTANT_ROLES)),
) -> ChatResponse:
    try:
        reply, conversation_id = await run_turn(
            control_db=control_db,
            tenant_db=tenant_db,
            org=org,
            user=user,
            entity_id=entity_id,
            conversation_id=body.conversation_id,
            message=body.message,
        )
    except AssistantBudgetExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "detail": "Monthly AI assistant token budget exceeded.",
                "code": "assistant_budget_exceeded",
                "used": exc.used,
                "budget": exc.budget,
                "period": exc.period,
            },
        )

    return ChatResponse(
        conversation_id=conversation_id,
        answer=reply.answer,
        tool_invocations=[
            ToolInvocationOut(tool=inv.tool, args=inv.args, result=inv.result, error=inv.error)
            for inv in reply.tool_invocations
        ],
        usage=UsageDelta(input_tokens=reply.input_tokens, output_tokens=reply.output_tokens),
    )


@router.get("/conversations", response_model=ConversationListResponse)
async def list_conversations(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    tenant_db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(*_ASSISTANT_ROLES)),
) -> ConversationListResponse:
    base = (
        select(Conversation)
        .where(Conversation.organization_id == org.id)
        .where(Conversation.user_id == user.id)
    )
    total = (
        await tenant_db.execute(select(func.count()).select_from(base.subquery()))
    ).scalar_one()
    convs = (
        (
            await tenant_db.execute(
                base.order_by(Conversation.updated_at.desc()).limit(limit).offset(offset)
            )
        )
        .scalars()
        .all()
    )

    items = []
    for conv in convs:
        count = (
            await tenant_db.execute(
                select(func.count())
                .select_from(ConversationMessage)
                .where(ConversationMessage.conversation_id == conv.id)
            )
        ).scalar_one()
        items.append(
            ConversationSummary(
                id=conv.id,
                title=conv.title,
                created_at=conv.created_at,
                updated_at=conv.updated_at,
                message_count=int(count),
            )
        )
    return ConversationListResponse(items=items, total=int(total))


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: uuid.UUID,
    tenant_db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(*_ASSISTANT_ROLES)),
) -> ConversationDetail:
    conv = (
        await tenant_db.execute(
            select(Conversation)
            .where(Conversation.id == conversation_id)
            .where(Conversation.organization_id == org.id)
            .where(Conversation.user_id == user.id)
        )
    ).scalar_one_or_none()
    # 404 (not 403) for someone else's / another tenant's id so it can't enumerate.
    if conv is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    msgs = (
        (
            await tenant_db.execute(
                select(ConversationMessage)
                .where(ConversationMessage.conversation_id == conv.id)
                .order_by(ConversationMessage.created_at.asc())
            )
        )
        .scalars()
        .all()
    )

    return ConversationDetail(
        conversation=ConversationSummary(
            id=conv.id,
            title=conv.title,
            created_at=conv.created_at,
            updated_at=conv.updated_at,
            message_count=len(msgs),
        ),
        messages=[
            MessageOut(
                id=m.id,
                role=m.role,
                content=m.content,
                tool_calls=_invocations_out(m.tool_calls),
                created_at=m.created_at,
            )
            for m in msgs
        ],
    )


@router.get("/usage", response_model=UsageResponse)
async def get_usage(
    control_db: AsyncSession = Depends(get_control_db),
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(*_ASSISTANT_ROLES)),
) -> UsageResponse:
    snapshot = await usage_service.get_usage_snapshot(control_db, org)
    return UsageResponse(**snapshot)
