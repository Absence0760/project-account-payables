"""Assistant token-budget meter (control plane).

``assert_within_budget`` gates a turn at the top of the orchestrator; ``record``
upserts the per-``(org, period)`` counter after a successful turn. The
``assistant_usage`` row is the single source of truth for the cap and for
``GET /api/assistant/usage``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.assistant import AssistantUsage
from app.models.organization import Organization


class AssistantBudgetExceeded(Exception):
    """Raised when the org's monthly token budget is exhausted."""

    def __init__(self, *, used: int, budget: int, period: str):
        self.used = used
        self.budget = budget
        self.period = period
        super().__init__(f"Assistant token budget exceeded: {used}/{budget} for {period}")


def _current_period() -> str:
    return datetime.now(UTC).strftime("%Y-%m")


def _budget_for(org: Organization) -> int:
    """Per-org override wins; else the platform default."""
    override = ((org.settings or {}).get("assistant") or {}).get("monthly_token_budget")
    try:
        if override is not None:
            return int(override)
    except (TypeError, ValueError):
        pass
    return settings.assistant_monthly_token_budget


async def _get_usage(
    control_db: AsyncSession, org_id: uuid.UUID, period: str
) -> AssistantUsage | None:
    return (
        await control_db.execute(
            select(AssistantUsage).where(
                AssistantUsage.organization_id == org_id,
                AssistantUsage.period == period,
            )
        )
    ).scalar_one_or_none()


async def _lock_meter_row(
    control_db: AsyncSession, org_id: uuid.UUID, period: str
) -> AssistantUsage | None:
    """``SELECT … FOR UPDATE`` the ``(org, period)`` meter row. Returns
    ``None`` when no row exists yet (first turn of the period) — the upsert
    in :func:`record` then creates it.

    The caller (:func:`assert_within_budget`) commits right after reading
    this, releasing the lock immediately rather than holding it across the
    model call — see that function's docstring for why.
    """
    return (
        await control_db.execute(
            select(AssistantUsage)
            .where(
                AssistantUsage.organization_id == org_id,
                AssistantUsage.period == period,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()


async def assert_within_budget(control_db: AsyncSession, org: Organization) -> None:
    """Raise :class:`AssistantBudgetExceeded` if the org is at/over its cap.

    Takes a row-level lock on the meter to make the check atomic, but commits
    immediately after reading it — the lock is held only for this one quick
    round-trip, NOT across the model call / SSE stream that follows. Holding
    it for the whole turn (the previous behavior) serialized an org to one
    in-flight ``/chat`` request at a time, since every other turn for that org
    blocked on the same ``FOR UPDATE`` row until the first turn's response
    finished streaming and the control transaction committed at request end.

    This trades perfect serialization for a bounded race: two turns that both
    start within the same short check window can both read ``used < budget``
    and both proceed, so the cap can be overshot by at most a handful of
    concurrent turns' worth of tokens before the next turn's check catches it.
    That's an acceptable trade for a soft usage-shaping guardrail (not a money
    invariant) — an indefinitely long lock is a worse bug than a small,
    self-correcting overshoot.

    Budget ``0`` disables the cap (matches ``FEOH_MAX_CONCURRENT_SESSIONS=0``).
    """
    budget = _budget_for(org)
    if budget <= 0:
        return
    period = _current_period()
    row = await _lock_meter_row(control_db, org.id, period)
    used = (row.input_tokens + row.output_tokens) if row else 0
    over_budget = used >= budget
    # Release the row lock right away — don't hold it across the model call.
    await control_db.commit()
    if over_budget:
        raise AssistantBudgetExceeded(used=used, budget=budget, period=period)


async def record(
    control_db: AsyncSession,
    org: Organization,
    in_tokens: int,
    out_tokens: int,
) -> None:
    """Upsert the ``(org, period)`` meter — accumulate this turn's tokens.

    Does **not** commit. The increment is flushed onto ``control_db`` and
    committed by the request lifecycle (``get_control_db``'s exit), the same
    boundary that commits the tenant-side conversation + audit rows. Binding
    them together means a turn that unwinds after this point (e.g. a failing
    ``_persist_turn``) rolls the tokens back too — usage can't be debited for a
    turn whose conversation/audit rows never landed.
    """
    period = _current_period()
    stmt = (
        insert(AssistantUsage)
        .values(
            id=uuid.uuid4(),
            organization_id=org.id,
            period=period,
            input_tokens=in_tokens,
            output_tokens=out_tokens,
            request_count=1,
        )
        .on_conflict_do_update(
            constraint="uq_assistant_usage_org_period",
            set_={
                "input_tokens": AssistantUsage.input_tokens + in_tokens,
                "output_tokens": AssistantUsage.output_tokens + out_tokens,
                "request_count": AssistantUsage.request_count + 1,
                "updated_at": datetime.now(UTC),
            },
        )
    )
    await control_db.execute(stmt)
    await control_db.flush()


async def get_usage_snapshot(control_db: AsyncSession, org: Organization) -> dict:
    """For ``GET /api/assistant/usage`` — current period meter + budget."""
    period = _current_period()
    row = await _get_usage(control_db, org.id, period)
    in_tok = row.input_tokens if row else 0
    out_tok = row.output_tokens if row else 0
    req = row.request_count if row else 0
    budget = _budget_for(org)
    total = in_tok + out_tok
    remaining = max(0, budget - total) if budget > 0 else 0
    return {
        "period": period,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "total_tokens": total,
        "budget": budget,
        "remaining": remaining,
        "request_count": req,
    }
