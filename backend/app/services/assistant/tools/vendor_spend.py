"""``get_vendor_spend`` tool — top-N vendor spend over a period.

Wraps ``services.analytics.compute_supplier_concentration``. The committed-status
set is lifted from ``app/api/analytics.py`` (imported, not duplicated). All sums
are ``Numeric``/``Decimal``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.analytics import _COMMITTED_STATUSES
from app.models.invoice import Invoice
from app.services.analytics import compute_supplier_concentration
from app.services.assistant.tools._currency import resolve_org_currency
from app.services.assistant.tools.schemas import (
    VendorSpendParams,
    VendorSpendResult,
    VendorSpendRow,
)
from app.tenant import apply_entity_scope

_PERIOD_LABELS = {
    "mtd": "month-to-date",
    "qtd": "quarter-to-date",
    "ytd": "year-to-date",
    "last_30d": "last 30 days",
    "last_90d": "last 90 days",
    "last_12m": "last 12 months",
}


def _period_start(period: str, today: date) -> date:
    if period == "mtd":
        return today.replace(day=1)
    if period == "qtd":
        q_first_month = ((today.month - 1) // 3) * 3 + 1
        return today.replace(month=q_first_month, day=1)
    if period == "ytd":
        return today.replace(month=1, day=1)
    if period == "last_30d":
        return today - timedelta(days=30)
    if period == "last_90d":
        return today - timedelta(days=90)
    if period == "last_12m":
        return today - timedelta(days=365)
    return today.replace(month=1, day=1)


async def get_vendor_spend(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    entity_id: uuid.UUID | None,
    current_user_id: uuid.UUID,
    params: VendorSpendParams,
    control_db: AsyncSession | None = None,
) -> VendorSpendResult:
    today = datetime.now(UTC).date()
    start = _period_start(params.period, today)

    stmt = (
        select(
            Invoice.vendor_id,
            Invoice.vendor_name,
            func.sum(Invoice.amount).label("total"),
        )
        .where(Invoice.status.in_(_COMMITTED_STATUSES))
        .where(Invoice.invoice_date >= start)
        .group_by(Invoice.vendor_id, Invoice.vendor_name)
    )
    stmt = apply_entity_scope(stmt, Invoice, entity_id)
    rows = (await db.execute(stmt)).all()

    # Shape into the dicts compute_supplier_concentration expects, sorted desc.
    vendor_spend = sorted(
        (
            {
                "vendor": vendor_name or "",
                "vendor_id": str(vendor_id) if vendor_id else None,
                "amount": Decimal(str(total or "0")),
            }
            for vendor_id, vendor_name, total in rows
        ),
        key=lambda r: r["amount"],
        reverse=True,
    )

    snapshot = compute_supplier_concentration(vendor_spend)
    total_spend = snapshot.total_spend

    currency = await resolve_org_currency(org_id, control_db)

    out_rows: list[VendorSpendRow] = []
    for r in vendor_spend[: params.top_n]:
        amount = r["amount"]
        share = (
            (amount / total_spend * Decimal("100")).quantize(Decimal("0.1"))
            if total_spend > 0
            else Decimal("0.0")
        )
        out_rows.append(
            VendorSpendRow(
                vendor_id=r["vendor_id"],
                vendor_name=r["vendor"],
                amount=amount.quantize(Decimal("0.01")),
                share_pct=share,
            )
        )

    return VendorSpendResult(
        period_label=_PERIOD_LABELS.get(params.period, params.period),
        currency=currency,
        total_spend=total_spend,
        vendors=out_rows,
    )
