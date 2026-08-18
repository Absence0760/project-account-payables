"""AI Cash-Flow Copilot API — a thin, finance-leader-gated façade over the
existing conversational-assistant orchestrator.

Phase 1: two routes (`POST /api/cash-flow/copilot` + its SSE
`/stream` variant) that reuse ``app.services.assistant.orchestrator`` exactly
like ``app/api/assistant.py``'s ``chat`` / ``chat_stream`` — same deps
(``get_tenant_db`` / ``get_control_db`` / ``get_tenant`` / ``get_entity_id``),
the same tenant isolation + budget gate + audit trail + SSE contract — but
gated to finance-leader roles only (``admin`` / ``ap_manager`` / ``cfo`` — NOT
``ap_clerk``) and behind the ``FEOH_CASHFLOW_COPILOT_ENABLED`` kill switch (both
routes 404 when disabled, so the surface simply doesn't exist when off).

Phase 3: two enact routes (draft-run / capture-discounts) — see
``docs/cash-flow-copilot.md`` §5/§6 for the full safety model. Both take the
plan's own resolved defining parameters back in the body
(``CashFlowPlanReplay``), recompute the deterministic ``plan_id``
(``services.cash_flow_plan.compute_plan_id``), and 409 if it doesn't match
the URL — a stale/mismatched plan is refused rather than silently acted on.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.analytics import _commitment_rows
from app.api.deps import (
    ROLE_ADMIN,
    ROLE_AP_MANAGER,
    ROLE_CFO,
    require_roles,
)
from app.api.payments import PAYABLE_INVOICE_STATUSES
from app.config import settings
from app.database import get_control_db
from app.models.discount import OFFER_STATUS_OFFERED
from app.models.invoice import Invoice
from app.models.organization import Organization
from app.models.user import User
from app.schemas.assistant import (
    ChatRequest,
    ChatResponse,
    ToolInvocationOut,
    UsageDelta,
)
from app.schemas.cash_flow import (
    CaptureDiscountsResponse,
    CashFlowPlanReplay,
    DraftRunResponse,
)
from app.services import discount_offers as offers_svc
from app.services.assistant import usage as usage_service
from app.services.assistant.orchestrator import run_turn, run_turn_streaming
from app.services.assistant.tools.cashflow import _horizon
from app.services.assistant.tools.optimizer import _cost_of_capital, run_discount_optimization
from app.services.assistant.usage import AssistantBudgetExceeded
from app.services.audit_dispatch import dispatch_audit
from app.services.cash_flow_plan import compute_plan_id
from app.services.cashflow import resolve_cash_thresholds
from app.services.currency_conversion import resolve_reporting_currency
from app.services.payment_runs import PaymentRunItemInput, create_payment_run_for_invoices
from app.tenant import get_entity_id, get_tenant, get_tenant_db, get_write_entity_id

router = APIRouter(prefix="/cash-flow", tags=["cash-flow"])

# Finance-leader roles ONLY — the copilot reasons about the money outflow plan,
# so it excludes ``ap_clerk`` (unlike the general assistant's role set). Public
# (not ``_``-prefixed) because it is the single answer to "who may see this
# org's cash position": ``services.cash_flow_alerts.ALERT_ROLES`` addresses the
# same audience and is pinned to this tuple by a drift-guard test, so the pull
# surface and the push surface can never disagree.
COPILOT_ROLES = (ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)


def _require_enabled() -> None:
    """Kill switch: when ``FEOH_CASHFLOW_COPILOT_ENABLED`` is off the whole
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
    user: User = Depends(require_roles(*COPILOT_ROLES)),
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
    user: User = Depends(require_roles(*COPILOT_ROLES)),
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


# ---------------------------------------------------------------------------
# Phase 3 — draft-only enactment (§5/§6). Both routes below:
#   * are gated to the SAME finance-leader roles + kill switch as /copilot;
#   * take the plan's own resolved parameters back (`CashFlowPlanReplay`) and
#     refuse (409) if they don't hash to the `plan_id` in the URL;
#   * never move money — draft-run stages a `draft` PaymentRun (CFO-gated
#     /execute is completely unchanged), capture-discounts only flips
#     `DiscountOffer.status` (the CFO-gated payment run still funds).
# ---------------------------------------------------------------------------


async def _resolve_and_verify_plan(
    *,
    body: CashFlowPlanReplay,
    plan_id: str,
    org_id: uuid.UUID,
    entity_id: uuid.UUID | None,
    control_db: AsyncSession,
    today: date,
) -> tuple[int, Decimal | None, Decimal]:
    """Resolve horizon_days / min_balance_threshold / cost_of_capital_pct from
    ``body`` exactly like ``propose_payment_plan`` resolves them, recompute
    the plan id from those RESOLVED values, and 409 if it doesn't match the
    URL's ``plan_id``.

    This is the stale-plan guard: the enact endpoints must never act on a
    plan the client's replay body no longer describes (edited params, or
    "today" has moved on since the plan was proposed).
    """
    horizon_days = _horizon(body.horizon_days)

    threshold = body.min_balance_threshold
    if threshold is None:
        org = await control_db.get(Organization, org_id)
        org_settings = (org.settings or {}) if org else {}
        threshold = resolve_cash_thresholds(org_settings).min_balance_threshold

    cost_of_capital = await _cost_of_capital(control_db, org_id, body.cost_of_capital_pct)

    expected_plan_id = compute_plan_id(
        org_id=org_id,
        entity_id=entity_id,
        granularity=body.granularity,
        horizon_days=horizon_days,
        min_balance_threshold=threshold,
        cash_budget=body.cash_budget,
        cost_of_capital_pct=cost_of_capital,
        today=today,
    )
    if expected_plan_id != plan_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This plan is stale — its parameters no longer match what was "
                "proposed (or today's date has moved on). Ask the copilot for a "
                "fresh plan before enacting it."
            ),
        )
    return horizon_days, threshold, cost_of_capital


@router.post("/plans/{plan_id}/draft-run", response_model=DraftRunResponse)
async def draft_run_from_plan(
    plan_id: str,
    body: CashFlowPlanReplay,
    response: Response,
    tenant_db: AsyncSession = Depends(get_tenant_db),
    control_db: AsyncSession = Depends(get_control_db),
    org: Organization = Depends(get_tenant),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
    write_entity_id: uuid.UUID = Depends(get_write_entity_id),
    user: User = Depends(require_roles(*COPILOT_ROLES)),
) -> DraftRunResponse:
    """Enact tier 1 of a proposed plan: stage a DRAFT payment run over every
    currently-payable invoice the plan's horizon says is due.

    Re-derives the SAME commitment rows ``propose_payment_plan`` used
    (``_commitment_rows`` — same params), narrows them to invoices that are
    ACTUALLY payable right now (``PAYABLE_INVOICE_STATUSES`` — a plan's
    horizon includes pre-approval pipeline invoices too, which can't be
    staged), and hands that set to the exact same
    ``services.payment_runs.create_payment_run_for_invoices`` the manual
    ``POST /api/payments/runs`` flow uses — same payable-status gate,
    financial-integrity block, credit-memo netting, and CFO-threshold
    computation. NEVER executes the run: it always lands ``status="draft"``,
    and execution stays behind the unchanged CFO-gated ``/execute`` path.

    Idempotent on ``plan_id``: retrying the same plan returns the SAME draft
    run (``created=False``, HTTP 200) instead of staging a second one.
    """
    _require_enabled()
    today = datetime.now(UTC).date()

    horizon_days, _threshold, _cost_of_capital = await _resolve_and_verify_plan(
        body=body,
        plan_id=plan_id,
        org_id=org.id,
        entity_id=entity_id,
        control_db=control_db,
        today=today,
    )

    rows = await _commitment_rows(
        tenant_db,
        today=today,
        horizon_days=horizon_days,
        include_pending=True,
        entity_id=entity_id,
        reporting_currency=resolve_reporting_currency(org.settings),
    )
    candidate_ids = {uuid.UUID(r["invoice_id"]) for r in rows if r.get("invoice_id")}
    if not candidate_ids:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This plan's horizon has no open commitments to stage.",
        )

    payable_result = await tenant_db.execute(
        select(Invoice.id).where(
            Invoice.id.in_(candidate_ids),
            Invoice.status.in_(PAYABLE_INVOICE_STATUSES),
        )
    )
    payable_ids = [row[0] for row in payable_result.all()]
    if not payable_ids:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "None of this plan's commitments are approved for payment yet — nothing to stage."
            ),
        )

    items = [PaymentRunItemInput(invoice_id=iid) for iid in payable_ids]
    result = await create_payment_run_for_invoices(
        tenant_db,
        org=org,
        org_id=org.id,
        entity_id=write_entity_id,
        # The SELECTED entity, the same one `_commitment_rows` above was scoped
        # by — so the run can only ever stage invoices the caller's own view
        # includes. `None` (consolidated) leaves the lookup unrestricted, which
        # is what every other consolidated read does.
        scope_entity_id=entity_id,
        user=user,
        items=items,
        plan_id=plan_id,
    )
    await tenant_db.commit()

    # Convention shared with e.g. positive_pay.py: 201 on genuine creation,
    # 200 on an idempotent replay of an already-existing run.
    response.status_code = status.HTTP_201_CREATED if result.created else status.HTTP_200_OK
    return DraftRunResponse(
        plan_id=plan_id,
        created=result.created,
        run_id=result.run.id,
        status=result.run.status,
        total_amount=result.total_amount,
        payment_count=result.payment_count,
        requires_cfo_approval=result.run.requires_cfo_approval,
    )


@router.post("/plans/{plan_id}/capture-discounts", response_model=CaptureDiscountsResponse)
async def capture_discounts_from_plan(
    plan_id: str,
    body: CashFlowPlanReplay,
    tenant_db: AsyncSession = Depends(get_tenant_db),
    control_db: AsyncSession = Depends(get_control_db),
    org: Organization = Depends(get_tenant),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
    user: User = Depends(require_roles(*COPILOT_ROLES)),
) -> CaptureDiscountsResponse:
    """Enact tier 2 of a proposed plan: accept every discount offer the SAME
    optimizer pass selected.

    Re-runs ``run_discount_optimization`` with the plan's own (verified)
    params to get the current ``selected`` offer ids — the identical pass
    ``propose_payment_plan`` and ``optimize_discount_capture`` use, so this
    can never accept something the plan didn't recommend — then calls the
    EXISTING accept path (``services.discount_offers.accept_offer``, the same
    mutator ``POST /api/discounts/offers/{id}/accept`` uses) per offer.
    **Status-only — never moves money**; the CFO-gated payment run still
    funds. An offer no longer ``offered`` (already accepted/captured/
    declined/expired — e.g. a prior call, or a manual accept in the
    meantime) is skipped, not re-raised: a second call is a clean no-op.
    """
    _require_enabled()
    today = datetime.now(UTC).date()

    _horizon_days, _threshold, cost_of_capital = await _resolve_and_verify_plan(
        body=body,
        plan_id=plan_id,
        org_id=org.id,
        entity_id=entity_id,
        control_db=control_db,
        today=today,
    )

    optimizer_result, offers = await run_discount_optimization(
        tenant_db,
        org_id=org.id,
        entity_id=entity_id,
        control_db=control_db,
        cash_budget=body.cash_budget,
        cost_of_capital_pct=cost_of_capital,
        today=today,
    )
    offer_by_id = {str(o.id): o for o in offers}

    accepted_ids: list[str] = []
    selected_count = 0
    for rec in optimizer_result.recommendations:
        if not rec.selected:
            continue
        selected_count += 1
        offer = offer_by_id.get(rec.opportunity.offer_id)
        if offer is None or offer.status != OFFER_STATUS_OFFERED:
            continue  # already handled (accepted/captured/declined/expired) — no-op
        tier = offers_svc.select_tier_for_date(
            offer.tiers or [],
            rec.opportunity.tier_days,
            today,
            offer.valid_until,
            reference_date=offer.valid_from,
        )
        if tier is None:
            continue  # this rung's window closed between optimize() and now
        try:
            offers_svc.accept_offer(offer, tier=tier, actor_id=user.id, now=datetime.now(UTC))
        except ValueError:
            continue  # raced with something else between the check and here
        await dispatch_audit(
            tenant_db,
            correlation_id=uuid.uuid4(),
            organization_id=org.id,
            actor_id=user.id,
            action="discount_offer.accepted",
            entity_type="discount_offer",
            entity_id=offer.id,
            details={"tier": offer.accepted_tier, "via": "cashflow_copilot"},
        )
        accepted_ids.append(str(offer.id))

    await tenant_db.commit()

    return CaptureDiscountsResponse(
        plan_id=plan_id,
        accepted_offer_ids=accepted_ids,
        accepted_count=len(accepted_ids),
        skipped_count=selected_count - len(accepted_ids),
        total_savings_selected=optimizer_result.total_savings_selected,
    )
