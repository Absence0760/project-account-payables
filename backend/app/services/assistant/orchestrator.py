"""Assistant orchestrator — the single entry point for a chat turn.

``run_turn`` owns the budget gate, conversation load/create, the tenant-bound +
audited ``run_tool`` closure, adapter dispatch, token accounting, and
persistence. Tenant isolation and audit logging live HERE (not in the adapters),
so a leaked/spoofed header can't widen access and every tool call is logged.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.assistant import Conversation, ConversationMessage
from app.models.organization import Organization
from app.models.user import User
from app.services.assistant import usage
from app.services.assistant.base import (
    AssistantReply,
    RunTool,
    StreamDone,
    TextDelta,
    ToolDelta,
    ToolInvocation,
)
from app.services.assistant.dispatcher import get_assistant_adapter
from app.services.assistant.sse import (
    sse_done,
    sse_error,
    sse_text_delta,
    sse_tool_event,
)
from app.services.assistant.tools import TOOL_SPECS, TOOLS
from app.services.audit_dispatch import dispatch_audit

# Cap how much prior context we replay to the adapter.
_HISTORY_TURNS = 20


def _assistant_config(org: Organization) -> dict:
    return {
        "provider": settings.assistant_provider,
        "api_key": settings.anthropic_api_key,
        "model": settings.assistant_model or settings.extraction_model,
        # Ollama adapter knobs — a dedicated tool-capable local text model and
        # base URL, kept separate from the claude-flavoured `model` above.
        "ollama_model": settings.assistant_ollama_model,
        "ollama_base_url": settings.ollama_base_url,
    }


def _safe_args_summary(tool_name: str, params) -> dict:
    """PII-safe arg SHAPE — filter shape, never values. No query text, no
    amounts, no bank/tax fields ever enter the audit row."""
    if tool_name == "list_invoices":
        return {
            "status": [s.value if hasattr(s, "value") else s for s in (params.status or [])],
            "has_vendor_filter": params.vendor_name is not None,
            "has_amount_filter": params.amount_min is not None or params.amount_max is not None,
            "has_date_filter": params.date_from is not None or params.date_to is not None,
            "limit": params.limit,
            "offset": params.offset,
        }
    if tool_name == "get_vendor_spend":
        return {"period": params.period, "top_n": params.top_n}
    if tool_name == "list_pending_approvals":
        return {"assignee": params.assignee, "limit": params.limit}
    if tool_name == "get_payment_forecast":
        return {"horizon": params.horizon, "granularity": params.granularity}
    if tool_name == "find_invoices_by_text":
        return {"query_len": len(params.query), "k": params.k}
    return {}


async def _get_or_create_conversation(
    tenant_db: AsyncSession,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID | None,
) -> Conversation:
    if conversation_id is not None:
        conv = (
            await tenant_db.execute(
                select(Conversation).where(
                    Conversation.id == conversation_id,
                    Conversation.organization_id == org_id,
                    Conversation.user_id == user_id,
                )
            )
        ).scalar_one_or_none()
        if conv is not None:
            return conv
        # Unknown / not-yours id → start a fresh conversation rather than leak
        # whether the id exists.
    conv = Conversation(
        id=uuid.uuid4(),
        organization_id=org_id,
        user_id=user_id,
        title=None,
    )
    tenant_db.add(conv)
    await tenant_db.flush()
    return conv


async def _load_history(tenant_db: AsyncSession, conversation_id: uuid.UUID) -> list[dict]:
    rows = (
        (
            await tenant_db.execute(
                select(ConversationMessage)
                .where(ConversationMessage.conversation_id == conversation_id)
                .order_by(ConversationMessage.created_at.desc())
                .limit(_HISTORY_TURNS)
            )
        )
        .scalars()
        .all()
    )
    rows = list(reversed(rows))
    return [{"role": m.role, "content": m.content} for m in rows]


async def _persist_turn(
    tenant_db: AsyncSession,
    conv: Conversation,
    message: str,
    reply: AssistantReply,
) -> None:
    tenant_db.add(
        ConversationMessage(
            id=uuid.uuid4(),
            conversation_id=conv.id,
            role="user",
            content=message,
            tool_calls={},
        )
    )
    tenant_db.add(
        ConversationMessage(
            id=uuid.uuid4(),
            conversation_id=conv.id,
            role="assistant",
            content=reply.answer,
            tool_calls={
                "invocations": [
                    {
                        "tool": inv.tool,
                        "args": inv.args,
                        "result": inv.result,
                        "error": inv.error,
                    }
                    for inv in reply.tool_invocations
                ]
            },
            input_tokens=reply.input_tokens,
            output_tokens=reply.output_tokens,
        )
    )
    if conv.title is None:
        conv.title = (message[:60] + "…") if len(message) > 60 else message
    await tenant_db.flush()


def _build_run_tool(
    *,
    control_db: AsyncSession,
    tenant_db: AsyncSession,
    org: Organization,
    user: User,
    entity_id: uuid.UUID | None,
    conv: Conversation,
) -> RunTool:
    """Construct the tenant-bound, audited tool executor for one turn.

    Tenant isolation + audit logging live HERE (not in the adapters): every
    tool call writes a PII-safe ``assistant.tool_invoked`` audit row BEFORE
    returning data, and the tool's read runs inside a SAVEPOINT so a failing
    query rolls back just the tool, not the whole turn. Shared by both
    ``run_turn`` (non-streaming) and ``run_turn_streaming`` (SSE) so the two
    paths can never diverge on the security-critical bits.
    """

    async def run_tool(tool_name: str, raw_args: dict) -> ToolInvocation:
        spec = TOOLS.get(tool_name)
        if spec is None:
            await dispatch_audit(
                tenant_db,
                correlation_id=uuid.uuid4(),
                organization_id=org.id,
                actor_id=user.id,
                action="assistant.tool_invoked",
                entity_type="assistant_conversation",
                entity_id=conv.id,
                details={"tool": tool_name, "args": {}, "error": "unknown_tool"},
            )
            return ToolInvocation(
                tool=tool_name, args={}, result=None, error=f"Unknown tool: {tool_name}"
            )

        try:
            params = spec.param_model.model_validate(raw_args)
        except Exception as exc:  # noqa: BLE001
            await dispatch_audit(
                tenant_db,
                correlation_id=uuid.uuid4(),
                organization_id=org.id,
                actor_id=user.id,
                action="assistant.tool_invoked",
                entity_type="assistant_conversation",
                entity_id=conv.id,
                details={"tool": tool_name, "args": {}, "error": "invalid_args"},
            )
            return ToolInvocation(
                tool=tool_name,
                args={},
                result=None,
                error=f"Invalid arguments: {exc.__class__.__name__}",
            )

        args_summary = _safe_args_summary(tool_name, params)
        # Audit EVERY tool call (PII-safe shape) BEFORE returning data.
        await dispatch_audit(
            tenant_db,
            correlation_id=uuid.uuid4(),
            organization_id=org.id,
            actor_id=user.id,
            action="assistant.tool_invoked",
            entity_type="assistant_conversation",
            entity_id=conv.id,
            details={"tool": tool_name, "args": args_summary},
        )

        # Run the tool's read inside a SAVEPOINT so a failing query (e.g. a
        # schema-drifted tenant) rolls back just the tool, not the whole turn —
        # the audit row written above and the message persistence below survive.
        try:
            async with tenant_db.begin_nested():
                result = await spec.fn(
                    tenant_db,
                    org_id=org.id,
                    entity_id=entity_id,
                    current_user_id=user.id,
                    control_db=control_db,
                    params=params,
                )
        except Exception as exc:  # noqa: BLE001
            return ToolInvocation(
                tool=tool_name,
                args=args_summary,
                result=None,
                error=f"Tool failed: {exc.__class__.__name__}",
            )
        return ToolInvocation(
            tool=tool_name,
            args=args_summary,
            result=result.model_dump(mode="json"),
            error=None,
        )

    return run_tool


async def _prepare_turn(
    *,
    control_db: AsyncSession,
    tenant_db: AsyncSession,
    org: Organization,
    user: User,
    entity_id: uuid.UUID | None,
    conversation_id: uuid.UUID | None,
) -> tuple[Conversation, list[dict], RunTool, object]:
    """Shared turn setup: budget gate → conversation load/create → history →
    adapter → audited ``run_tool`` closure. Used by both the streaming and
    non-streaming paths so they share one budget/isolation/audit story.

    Raises ``AssistantBudgetExceeded`` (→ 429) when the org is over budget.
    """
    # 1. Budget gate — before any adapter/model/tool work.
    await usage.assert_within_budget(control_db, org)

    # 2. Load / create the conversation (scoped to org + user); load history.
    conv = await _get_or_create_conversation(tenant_db, org.id, user.id, conversation_id)
    history = await _load_history(tenant_db, conv.id)

    # 3. Build the adapter (mock by default; claude only when keyed).
    adapter = get_assistant_adapter(_assistant_config(org))

    # 4. Tenant-bound, audited tool executor.
    run_tool = _build_run_tool(
        control_db=control_db,
        tenant_db=tenant_db,
        org=org,
        user=user,
        entity_id=entity_id,
        conv=conv,
    )
    return conv, history, run_tool, adapter


async def run_turn(
    *,
    control_db: AsyncSession,
    tenant_db: AsyncSession,
    org: Organization,
    user: User,
    entity_id: uuid.UUID | None,
    conversation_id: uuid.UUID | None,
    message: str,
) -> tuple[AssistantReply, uuid.UUID]:
    """Execute one chat turn. Raises ``AssistantBudgetExceeded`` (→ 429) when the
    org is over its monthly token budget."""
    conv, history, run_tool, adapter = await _prepare_turn(
        control_db=control_db,
        tenant_db=tenant_db,
        org=org,
        user=user,
        entity_id=entity_id,
        conversation_id=conversation_id,
    )

    # Run the adapter (0..N tool calls via run_tool).
    reply = await adapter.respond(
        message=message,
        history=history,
        tool_specs=TOOL_SPECS,
        run_tool=run_tool,
    )

    # Persist the user + assistant messages (tenant DB) FIRST, so a
    # persistence failure aborts the turn before any token debit.
    await _persist_turn(tenant_db, conv, message, reply)

    # Record usage (control-plane upsert). No standalone commit — it shares
    # the request's commit boundary with the conversation + audit rows, so a
    # later failure rolls all three back together (no orphaned token debit).
    await usage.record(control_db, org, reply.input_tokens, reply.output_tokens)

    return reply, conv.id


async def run_turn_streaming(
    *,
    control_db: AsyncSession,
    tenant_db: AsyncSession,
    org: Organization,
    user: User,
    entity_id: uuid.UUID | None,
    conversation_id: uuid.UUID | None,
    message: str,
) -> AsyncIterator[str]:
    """Execute one chat turn, yielding SSE frames as the answer is produced.

    Iterates the adapter's ``respond_streaming`` generator and forwards its
    events onto the wire: a ``ToolDelta`` becomes a ``tool`` frame (emitted as
    the tool completes, before the prose that cites it), a ``TextDelta`` becomes
    a ``delta`` frame, and the terminal ``StreamDone`` carries the assembled
    reply used for persistence + the final ``done`` frame.

    On the ``claude`` adapter the ``TextDelta``s are **real Anthropic per-token
    ``text_delta``s forwarded as they arrive** — true token-by-token passthrough
    — while the audited ``run_tool`` still fires for every tool-use block. On
    ``mock`` / ``ollama`` the deltas are coarse post-hoc chunks of the assembled
    answer (the default ``respond_streaming``), so local-first + tests stay
    deterministic and network-free. Either path's concatenated ``delta`` text
    reconstructs ``done.answer`` exactly.

    **Transactional invariant.** Under ``StreamingResponse`` the request-scoped
    session teardown fires only after the body is fully drained, so we commit
    explicitly INSIDE the generator instead of relying on it: the conversation +
    audit rows (tenant) and the usage debit (control) are committed together,
    tenant first. Any failure rolls BOTH back and emits an ``error`` event, so
    usage is never charged for a turn whose rows didn't land. The dependency
    teardown's later ``commit()`` is then a no-op on the already-committed
    (or already-rolled-back) sessions.

    The budget gate must be applied by the CALLER (the endpoint) before this
    generator is iterated, so an over-budget org gets a real HTTP 429 rather
    than an in-stream error.
    """
    try:
        conv, history, run_tool, adapter = await _prepare_turn(
            control_db=control_db,
            tenant_db=tenant_db,
            org=org,
            user=user,
            entity_id=entity_id,
            conversation_id=conversation_id,
        )

        # Iterate the adapter's streaming generator, forwarding each event onto
        # the SSE wire as it arrives. The adapter runs the full tool-use loop
        # (audited via run_tool); `tool` frames are emitted as tools complete
        # and `delta` frames as text is produced (real per-token on claude). The
        # terminal StreamDone carries the assembled reply for persistence.
        reply: AssistantReply | None = None
        async for ev in adapter.respond_streaming(
            message=message,
            history=history,
            tool_specs=TOOL_SPECS,
            run_tool=run_tool,
        ):
            if isinstance(ev, ToolDelta):
                yield sse_tool_event(ev.invocation)
            elif isinstance(ev, TextDelta):
                # An empty span produces no frame (keeps the stream tidy).
                if ev.text:
                    yield sse_text_delta(ev.text)
            elif isinstance(ev, StreamDone):
                reply = ev.reply
        if reply is None:  # defensive — a well-behaved adapter always ends with StreamDone
            reply = AssistantReply(
                answer="I wasn't able to produce an answer for that.",
                provider="",
            )

        # Persist + record, then commit BOTH together (tenant first). This is
        # the single point where the turn's rows become durable.
        await _persist_turn(tenant_db, conv, message, reply)
        await usage.record(control_db, org, reply.input_tokens, reply.output_tokens)
        await tenant_db.commit()
        await control_db.commit()
    except Exception as exc:  # noqa: BLE001
        # Roll BOTH back so the dependency teardown can't re-commit a half turn,
        # and so usage is never debited for rows that didn't land.
        await tenant_db.rollback()
        await control_db.rollback()
        yield sse_error(code="stream_failed", detail=f"{exc.__class__.__name__}")
        return

    yield sse_done(
        conversation_id=conv.id,
        answer=reply.answer,
        tool_invocations=reply.tool_invocations,
        usage_in=reply.input_tokens,
        usage_out=reply.output_tokens,
    )
