"""AI Cash-Flow Copilot API — a thin, finance-leader-gated façade over the
existing conversational-assistant orchestrator.

Phase 1: two routes (`POST /api/cash-flow/copilot` + its SSE
`/stream` variant) that reuse ``app.services.assistant.orchestrator`` exactly
like ``app/api/assistant.py``'s ``chat`` / ``chat_stream`` — same deps
(``get_tenant_db`` / ``get_control_db`` / ``get_tenant`` / ``get_entity_id``),
the same tenant isolation + budget gate + audit trail + SSE contract — but
gated to finance-leader roles only (``admin`` / ``ap_manager`` / ``cfo`` — NOT
``ap_clerk``) and behind the ``AP_CASHFLOW_COPILOT_ENABLED`` kill switch (both
routes 404 when disabled, so the surface simply doesn't exist when off).

The Phase 3 enact routes (draft-run / capture-discounts) are intentionally NOT
here — see ``docs/cash-flow-copilot.md`` § 6 / § 11.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    ROLE_ADMIN,
    ROLE_AP_MANAGER,
    ROLE_CFO,
    require_roles,
)
from app.config import settings
from app.database import get_control_db
from app.models.organization import Organization
from app.models.user import User
from app.schemas.assistant import (
    ChatRequest,
    ChatResponse,
    ToolInvocationOut,
    UsageDelta,
)
from app.services.assistant import usage as usage_service
from app.services.assistant.orchestrator import run_turn, run_turn_streaming
from app.services.assistant.usage import AssistantBudgetExceeded
from app.tenant import get_entity_id, get_tenant, get_tenant_db

router = APIRouter(prefix="/cash-flow", tags=["cash-flow"])

# Finance-leader roles ONLY — the copilot reasons about the money outflow plan,
# so it excludes ``ap_clerk`` (unlike the general assistant's role set).
_COPILOT_ROLES = (ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)


def _require_enabled() -> None:
    """Kill switch: when ``AP_CASHFLOW_COPILOT_ENABLED`` is off the whole
    surface 404s, so a disabled copilot is indistinguishable from an unmounted
    route (it doesn't enumerate a feature the org hasn't turned on)."""
    if not settings.cashflow_copilot_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)


def _budget_exceeded_http(exc: AssistantBudgetExceeded) -> HTTPException:
    """The shared 429 mapping — identical body for ``/copilot`` and
    ``/copilot/stream`` (and to the assistant routes) so the frontend handles an
    over-budget org the same way everywhere."""
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail={
            "detail": "Monthly AI assistant token budget exceeded.",
            "code": "assistant_budget_exceeded",
            "used": exc.used,
            "budget": exc.budget,
            "period": exc.period,
        },
    )


@router.post("/copilot", response_model=ChatResponse)
async def copilot(
    body: ChatRequest,
    tenant_db: AsyncSession = Depends(get_tenant_db),
    control_db: AsyncSession = Depends(get_control_db),
    org: Organization = Depends(get_tenant),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
    user: User = Depends(require_roles(*_COPILOT_ROLES)),
) -> ChatResponse:
    """Finance-leader façade over ``orchestrator.run_turn`` — same body, deps,
    and budget→429 mapping as ``POST /api/assistant/chat``."""
    _require_enabled()
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
        raise _budget_exceeded_http(exc)

    return ChatResponse(
        conversation_id=conversation_id,
        answer=reply.answer,
        tool_invocations=[
            ToolInvocationOut(tool=inv.tool, args=inv.args, result=inv.result, error=inv.error)
            for inv in reply.tool_invocations
        ],
        usage=UsageDelta(input_tokens=reply.input_tokens, output_tokens=reply.output_tokens),
    )


@router.post("/copilot/stream")
async def copilot_stream(
    body: ChatRequest,
    tenant_db: AsyncSession = Depends(get_tenant_db),
    control_db: AsyncSession = Depends(get_control_db),
    org: Organization = Depends(get_tenant),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
    user: User = Depends(require_roles(*_COPILOT_ROLES)),
) -> StreamingResponse:
    """Streaming counterpart of ``POST /copilot`` — mirrors
    ``POST /api/assistant/chat/stream`` exactly (same SSE media type + headers,
    same ``tool``/``delta``/``done``/``error`` contract).

    The budget gate runs HERE, before the ``StreamingResponse`` is constructed,
    so an over-budget org gets a real HTTP 429 (same body as ``/copilot``)
    instead of an in-stream error the frontend would have to special-case.
    """
    _require_enabled()
    try:
        await usage_service.assert_within_budget(control_db, org)
    except AssistantBudgetExceeded as exc:
        raise _budget_exceeded_http(exc)

    generator = run_turn_streaming(
        control_db=control_db,
        tenant_db=tenant_db,
        org=org,
        user=user,
        entity_id=entity_id,
        conversation_id=body.conversation_id,
        message=body.message,
    )
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
