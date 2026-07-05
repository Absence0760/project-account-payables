"""``optimize_discount_capture`` tool — cash-constrained early-pay ranking.

Finance-leader-only (``ToolSpec.allowed_roles``). Wraps the same
``services.discount_optimizer.optimize`` pass the ``POST
/api/discounts/optimize`` endpoint runs, over the same open-offer set and the
same per-offer opportunity builder, so the copilot's recommendation can never
diverge from the discounts dashboard. Money is ``Decimal`` end to end.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.discounts import _build_opportunity, _name_maps
from app.config import settings
from app.models.discount import OFFER_STATUS_OFFERED, DiscountOffer
from app.models.organization import Organization
from app.services.assistant.tools._currency import resolve_org_currency
from app.services.assistant.tools.schemas import (
    DiscountRecommendation,
    OptimizeDiscountsParams,
    OptimizeDiscountsResult,
)
from app.services.discount_optimizer import optimize
from app.tenant import apply_entity_scope


async def _cost_of_capital(
    control_db: AsyncSession | None, org_id: uuid.UUID, override: Decimal | None
) -> Decimal:
    """Explicit param → org ``settings.discounting.cost_of_capital_pct`` →
    platform default. Mirrors ``api/discounts._cost_of_capital``."""
    if override is not None:
        return override
    if control_db is not None:
        org = await control_db.get(Organization, org_id)
        raw = (((org.settings if org else None) or {}).get("discounting") or {}).get(
            "cost_of_capital_pct"
        )
        if raw is not None:
            return Decimal(str(raw))
    return Decimal(str(settings.discount_cost_of_capital_pct))


async def optimize_discount_capture(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    entity_id: uuid.UUID | None,
    current_user_id: uuid.UUID,
    params: OptimizeDiscountsParams,
    control_db: AsyncSession | None = None,
) -> OptimizeDiscountsResult:
    today = datetime.now(UTC).date()
    cost_of_capital = await _cost_of_capital(control_db, org_id, params.cost_of_capital_pct)

    offers = list(
        (
            await db.execute(
                apply_entity_scope(
                    select(DiscountOffer).where(DiscountOffer.status == OFFER_STATUS_OFFERED),
                    DiscountOffer,
                    entity_id,
                )
            )
        )
        .scalars()
        .all()
    )
    offer_by_id = {str(o.id): o for o in offers}
    opportunities = []
    for offer in offers:
        opp = await _build_opportunity(db, offer, today=today)
        if opp is not None:
            opportunities.append(opp)
    vmap, imap = await _name_maps(db, offers)

    result = optimize(
        opportunities,
        cash_budget=params.cash_budget,
        cost_of_capital_pct=cost_of_capital,
        today=today,
    )

    recommendations: list[DiscountRecommendation] = []
    for r in result.recommendations:
        offer = offer_by_id.get(r.opportunity.offer_id)
        vendor_name = None
        invoice_number = None
        if offer is not None:
            if offer.vendor_id:
                vendor_name = vmap.get(offer.vendor_id)
            if offer.invoice_id and offer.invoice_id in imap:
                invoice_number, inv_vendor = imap[offer.invoice_id]
                vendor_name = vendor_name or inv_vendor
        recommendations.append(
            DiscountRecommendation(
                offer_id=r.opportunity.offer_id,
                vendor_name=vendor_name,
                invoice_number=invoice_number,
                base_amount=r.opportunity.base_amount,
                discount_percent=r.opportunity.discount_percent,
                annualized_return_pct=r.roi.annualized_return_pct,
                savings=r.roi.savings,
                pay_by=r.opportunity.pay_by.isoformat(),
                selected=r.selected,
            )
        )

    return OptimizeDiscountsResult(
        currency=await resolve_org_currency(org_id, control_db),
        cost_of_capital_pct=result.cost_of_capital_pct,
        total_savings_available=result.total_savings_available,
        total_savings_selected=result.total_savings_selected,
        total_outlay_selected=result.total_outlay_selected,
        recommendations=recommendations,
    )
