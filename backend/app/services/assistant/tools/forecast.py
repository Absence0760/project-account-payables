"""``get_payment_forecast`` tool — projected AP outflow buckets.

Wraps ``services.analytics.bucket_outflows``. Pulls due-dated obligations in
the committed + pending status sets (lifted from ``app/api/analytics.py``) whose
due date falls within the horizon, buckets them, and sums. Money is ``Decimal``
end to end.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.analytics import _COMMITTED_STATUSES, _PENDING_STATUSES
from app.models.invoice import Invoice
from app.services.analytics import bucket_outflows
from app.services.assistant.tools._currency import resolve_org_currency
from app.services.assistant.tools.schemas import (
    ForecastBucket,
    ForecastParams,
    ForecastResult,
)
from app.tenant import apply_entity_scope

_HORIZON_DAYS = {"7d": 7, "14d": 14, "30d": 30, "60d": 60, "90d": 90}
_HORIZON_LABELS = {
    "7d": "next 7 days",
    "14d": "next 14 days",
    "30d": "next 30 days",
    "60d": "next 60 days",
    "90d": "next 90 days",
}


async def get_payment_forecast(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    entity_id: uuid.UUID | None,
    current_user_id: uuid.UUID,
    params: ForecastParams,
    control_db: AsyncSession | None = None,
) -> ForecastResult:
    today = datetime.now(UTC).date()
    horizon_days = _HORIZON_DAYS[params.horizon]
    horizon_end = today + timedelta(days=horizon_days)
    committed_set = set(_COMMITTED_STATUSES)
    statuses = list(_COMMITTED_STATUSES) + list(_PENDING_STATUSES)

    stmt = (
        select(Invoice.due_date, Invoice.amount, Invoice.status)
        .where(Invoice.status.in_(statuses))
        .where(Invoice.due_date.isnot(None))
        .where(Invoice.due_date >= today)
        .where(Invoice.due_date <= horizon_end)
    )
    stmt = apply_entity_scope(stmt, Invoice, entity_id)
    rows = (await db.execute(stmt)).all()

    commitment_rows = [
        {
            "due_date": due,
            "amount": Decimal(str(amount or "0")),
            "committed": (status.value if hasattr(status, "value") else status) in committed_set,
        }
        for due, amount, status in rows
    ]

    buckets = bucket_outflows(commitment_rows, granularity=params.granularity, today=today)

    out_buckets: list[ForecastBucket] = []
    total = Decimal("0")
    for b in buckets:
        amount = Decimal(str(b["scheduled_amount"]))
        total += amount
        out_buckets.append(ForecastBucket(period=b["period"], amount=amount, count=int(b["count"])))

    currency = await resolve_org_currency(org_id, control_db)
    return ForecastResult(
        currency=currency,
        horizon_label=_HORIZON_LABELS[params.horizon],
        buckets=out_buckets,
        total=total.quantize(Decimal("0.01")),
    )
