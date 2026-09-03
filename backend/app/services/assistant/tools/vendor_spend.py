"""``get_vendor_spend`` tool — top-N vendor spend over a period.

Wraps ``services.analytics.compute_supplier_concentration``. The committed-status
set is lifted from ``app/api/analytics.py`` (imported, not duplicated). All sums
are ``Numeric``/``Decimal``.

Rolled into the org's reporting currency (not a naive SUM across currencies —
the same fix `app/api/analytics.py`'s supplier-concentration queries already
carry, via `reporting_amount_for_row`/`vendor_rollup_to_reporting_currency`):
a vendor billing in more than one currency, or a tenant with vendors in
different currencies, used to add e.g. USD + EUR amounts as if they were one
currency and hand the mixed total to the assistant labeled with a single
currency code.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
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
from app.services.currency_conversion import reporting_amount_for_row
from app.tenant import apply_entity_scope
from app.utils.dates import utc_today

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
    today = utc_today()
    start = _period_start(params.period, today)
    currency = await resolve_org_currency(org_id, control_db)

    stmt = (
        select(
            Invoice.vendor_id,
            Invoice.vendor_name,
            Invoice.amount,
            Invoice.currency,
            Invoice.reporting_amount,
            Invoice.reporting_currency,
        )
        .where(Invoice.status.in_(_COMMITTED_STATUSES))
        .where(Invoice.invoice_date >= start)
    )
    stmt = apply_entity_scope(stmt, Invoice, entity_id)
    rows = (await db.execute(stmt)).all()

    # Convert each row into the reporting currency BEFORE grouping by vendor —
    # summing raw `amount` across vendors/rows would add unlike face values.
    by_vendor: dict[tuple[str | None, str], Decimal] = {}
    for vendor_id, vendor_name, amount, inv_currency, rep_amount, rep_currency in rows:
        converted, _unconverted = reporting_amount_for_row(
            amount=Decimal(str(amount or 0)),
            currency=inv_currency,
            reporting_currency=currency,
            persisted_reporting_currency=rep_currency,
            persisted_reporting_amount=rep_amount,
        )
        key = (str(vendor_id) if vendor_id else None, vendor_name or "")
        by_vendor[key] = by_vendor.get(key, Decimal("0")) + converted

    # Shape into the dicts compute_supplier_concentration expects, sorted desc.
    vendor_spend = sorted(
        (
            {"vendor": vendor_name, "vendor_id": vendor_id, "amount": amount}
            for (vendor_id, vendor_name), amount in by_vendor.items()
        ),
        key=lambda r: r["amount"],
        reverse=True,
    )

    snapshot = compute_supplier_concentration(vendor_spend)
    total_spend = snapshot.total_spend

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
