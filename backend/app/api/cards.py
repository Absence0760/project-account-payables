"""Virtual card endpoints — generate, list, cancel, webhook."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import extract, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    ROLE_ADMIN,
    ROLE_AP_MANAGER,
    ROLE_CFO,
    get_org_id,
    require_roles,
)
from app.config import settings
from app.models.invoice import Invoice
from app.models.organization import Organization
from app.models.user import User
from app.models.vendor import Vendor
from app.models.virtual_card import CardRebate, VirtualCard
from app.schemas.virtual_card import (
    CardDashboardResponse,
    CardDetailsResponse,
    CardListResponse,
    CardResponse,
    GenerateCardsRequest,
    RebateListResponse,
    RebateResponse,
)
from app.tenant import get_tenant, get_tenant_db

router = APIRouter(prefix="/cards", tags=["cards"])


def _resolve_card_config(org: Organization) -> dict:
    """Build card adapter config based on program type.

    - "platform": use platform-level keys from app settings (you earn rebates)
    - "byok": use customer's own keys from org settings (they earn rebates)
    """
    org_cards = (org.settings or {}).get("cards", {})
    program_type = org_cards.get("program_type", "platform")
    region = org_cards.get("region", "US")

    if program_type == "byok":
        # Customer provided their own keys
        return {
            "provider": org_cards.get("provider", ""),
            "region": region,
            "api_key": org_cards.get("api_key", ""),
            "client_id": org_cards.get("client_id", ""),
            "client_secret": org_cards.get("client_secret", ""),
            "customer_hash_id": org_cards.get("customer_hash_id", ""),
            "wallet_hash_id": org_cards.get("wallet_hash_id", ""),
            "sandbox": org_cards.get("sandbox", True),
        }
    else:
        # Platform keys — auto-select provider by region
        from app.services.card_adapters.dispatcher import get_default_provider

        provider = get_default_provider(region)

        if provider == "lithic":
            return {
                "provider": "lithic",
                "region": region,
                "api_key": settings.lithic_api_key,
                "sandbox": settings.lithic_sandbox,
            }
        else:
            return {
                "provider": "nium",
                "region": region,
                "client_id": settings.nium_client_id,
                "client_secret": settings.nium_client_secret,
                "customer_hash_id": settings.nium_customer_hash_id,
                "wallet_hash_id": settings.nium_wallet_hash_id,
                "sandbox": settings.nium_sandbox,
            }


def _card_response(
    card: VirtualCard, invoice: Invoice | None = None, vendor: Vendor | None = None
) -> CardResponse:
    return CardResponse(
        id=str(card.id),
        invoice_id=str(card.invoice_id),
        vendor_id=str(card.vendor_id) if card.vendor_id else None,
        card_provider=card.card_provider,
        last_four=card.last_four,
        amount_limit=float(card.amount_limit),
        amount_charged=float(card.amount_charged) if card.amount_charged else None,
        currency=card.currency,
        status=card.status,
        expires_at=card.expires_at.isoformat() if card.expires_at else None,
        sent_at=card.sent_at.isoformat() if card.sent_at else None,
        charged_at=card.charged_at.isoformat() if card.charged_at else None,
        merchant_name=card.merchant_name,
        decline_reason=card.decline_reason,
        created_at=card.created_at.isoformat() if card.created_at else "",
        vendor_name=invoice.vendor_name if invoice else (vendor.name if vendor else None),
        invoice_number=invoice.invoice_number if invoice else None,
    )


@router.get("", response_model=CardListResponse)
async def list_cards(
    status_filter: str | None = Query(None, alias="status"),
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)),
):
    query = select(VirtualCard, Invoice).outerjoin(Invoice, VirtualCard.invoice_id == Invoice.id)
    if status_filter:
        statuses = [s.strip() for s in status_filter.split(",")]
        query = query.where(VirtualCard.status.in_(statuses))

    query = query.order_by(VirtualCard.created_at.desc())
    result = await db.execute(query)
    rows = result.all()

    return CardListResponse(
        items=[_card_response(card, inv) for card, inv in rows],
        total=len(rows),
    )


@router.get("/dashboard", response_model=CardDashboardResponse)
async def card_dashboard(
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)),
):
    now = datetime.now(UTC)

    # Active cards
    active_q = select(func.count(), func.coalesce(func.sum(VirtualCard.amount_limit), 0)).where(
        VirtualCard.status.in_(["created", "sent", "active"])
    )
    active_result = await db.execute(active_q)
    active_count, active_value = active_result.one()

    # Spend this month
    spend_q = select(func.coalesce(func.sum(VirtualCard.amount_charged), 0)).where(
        VirtualCard.status.in_(["charged", "completed"]),
        extract("month", VirtualCard.charged_at) == now.month,
        extract("year", VirtualCard.charged_at) == now.year,
    )
    spend_result = await db.execute(spend_q)
    spend_this_month = spend_result.scalar() or 0

    # Rebates this month
    rebate_month_q = select(func.coalesce(func.sum(CardRebate.amount), 0)).where(
        CardRebate.period == now.strftime("%Y-%m")
    )
    rebate_month = (await db.execute(rebate_month_q)).scalar() or 0

    # Rebates YTD
    rebate_ytd_q = select(func.coalesce(func.sum(CardRebate.amount), 0)).where(
        CardRebate.period >= f"{now.year}-01"
    )
    rebate_ytd = (await db.execute(rebate_ytd_q)).scalar() or 0

    return CardDashboardResponse(
        active_cards=active_count or 0,
        active_cards_value=float(active_value),
        spend_this_month=float(spend_this_month),
        rebates_this_month=float(rebate_month),
        rebates_ytd=float(rebate_ytd),
    )


@router.post("/generate", response_model=CardListResponse, status_code=status.HTTP_201_CREATED)
async def generate_cards(
    body: GenerateCardsRequest,
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    org_cards = (org.settings or {}).get("cards", {})
    if not org_cards.get("enabled"):
        raise HTTPException(
            status_code=400,
            detail="Virtual cards are not enabled. Configure in Organization Settings.",
        )
    card_config = _resolve_card_config(org)

    # Import adapters
    import app.services.card_adapters.lithic  # noqa: F401
    import app.services.card_adapters.mock_adapter  # noqa: F401
    import app.services.card_adapters.nium  # noqa: F401
    from app.services.card_adapters import VirtualCardPayload, get_card_adapter

    adapter = get_card_adapter(card_config)
    expiry_days = card_config.get("default_expiry_days", 30)

    # Load invoices
    ids = [uuid.UUID(i) for i in body.invoice_ids]
    result = await db.execute(select(Invoice).where(Invoice.id.in_(ids)))
    invoices = result.scalars().all()

    cards: list[VirtualCard] = []
    for inv in invoices:
        payload = VirtualCardPayload(
            correlation_id=str(inv.correlation_id),
            invoice_id=str(inv.id),
            vendor_name=inv.vendor_name,
            vendor_email=None,
            amount=inv.amount,
            currency=inv.currency or "USD",
            description=inv.description,
            expiry_days=expiry_days,
        )

        card_result = await adapter.create_card(payload)
        if not card_result.success:
            continue  # skip failed cards, don't block the batch

        card = VirtualCard(
            invoice_id=inv.id,
            vendor_id=inv.vendor_id,
            correlation_id=inv.correlation_id,
            card_provider=adapter.provider_name,
            provider_card_id=card_result.provider_card_id or "",
            last_four=card_result.last_four,
            amount_limit=inv.amount,
            currency=inv.currency or "USD",
            status="created",
            expires_at=datetime.now(UTC) + timedelta(days=expiry_days),
            organization_id=org_id,
        )
        db.add(card)
        cards.append(card)

    await db.flush()
    await db.commit()

    return CardListResponse(
        items=[_card_response(c) for c in cards],
        total=len(cards),
    )


@router.get("/{card_id}/details", response_model=CardDetailsResponse)
async def get_card_details(
    card_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)),
):
    """Retrieve full card details. Restricted to admin/manager roles. Audit-logged."""
    # Role check — only admin and ap_manager can see full card details
    user_roles = {r.name for r in user.roles}
    if not user_roles & {"admin", "ap_manager"}:
        raise HTTPException(
            status_code=403, detail="Only admins and AP managers can view card details"
        )

    result = await db.execute(select(VirtualCard).where(VirtualCard.id == card_id))
    card = result.scalar_one_or_none()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")

    # Audit log the access
    from app.services.audit_dispatch import dispatch_audit

    await dispatch_audit(
        db,
        correlation_id=card.correlation_id or uuid.uuid4(),
        organization_id=card.organization_id,
        actor_id=user.id,
        action="card.details_viewed",
        entity_type="virtual_card",
        entity_id=card.id,
        details={"last_four": card.last_four},
    )

    card_config = _resolve_card_config(org)

    import app.services.card_adapters.lithic  # noqa: F401
    import app.services.card_adapters.mock_adapter  # noqa: F401
    import app.services.card_adapters.nium  # noqa: F401
    from app.services.card_adapters import get_card_adapter

    adapter = get_card_adapter(card_config)
    details = await adapter.get_card_details(card.provider_card_id)

    return CardDetailsResponse(
        card_number=details.card_number,
        exp_month=details.exp_month,
        exp_year=details.exp_year,
        cvv=details.cvv,
    )


@router.post("/{card_id}/cancel")
async def cancel_card(
    card_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)),
):
    result = await db.execute(select(VirtualCard).where(VirtualCard.id == card_id))
    card = result.scalar_one_or_none()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")
    if card.status in ("charged", "completed", "cancelled"):
        raise HTTPException(status_code=409, detail=f"Cannot cancel card in '{card.status}' status")

    card_config = _resolve_card_config(org)

    import app.services.card_adapters.lithic  # noqa: F401
    import app.services.card_adapters.mock_adapter  # noqa: F401
    import app.services.card_adapters.nium  # noqa: F401
    from app.services.card_adapters import get_card_adapter

    adapter = get_card_adapter(card_config)
    await adapter.cancel_card(card.provider_card_id)
    card.status = "cancelled"
    await db.commit()

    return {"success": True, "message": "Card cancelled"}


@router.post("/webhook/{provider}")
async def card_webhook(provider: str, request: Request):
    """Handle charge/settlement webhooks from card providers."""
    body = await request.json()

    # Normalize by provider
    if provider == "lithic":
        card_token = body.get("card_token") or body.get("card", {}).get("token")
        event_type = body.get("type", "")
        amount = body.get("amount", 0)
        merchant = body.get("merchant", {}).get("descriptor")
    elif provider == "nium":
        card_token = body.get("cardHashId")
        event_type = body.get("eventType", "")
        amount = body.get("amount", 0)
        merchant = body.get("merchantName")
    else:
        return {"received": True, "action": "ignored"}

    if not card_token:
        return {"received": True, "action": "no_card_token"}

    # Find the card across all tenant DBs — in production, include tenant info in webhook metadata
    # For now, search by provider_card_id
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.database import control_session_factory, get_tenant_engine

    async with control_session_factory() as ctrl_db:
        orgs_result = await ctrl_db.execute(select(Organization))
        orgs = orgs_result.scalars().all()

    for org_obj in orgs:
        engine = get_tenant_engine(org_obj.db_name)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            try:
                result = await db.execute(
                    select(VirtualCard).where(VirtualCard.provider_card_id == card_token)
                )
                card = result.scalar_one_or_none()
                if not card:
                    continue

                # Update card based on event
                is_auth = "authorization" in event_type.lower() or "auth" in event_type.lower()
                is_settled = (
                    "transaction" in event_type.lower() or "settlement" in event_type.lower()
                )

                if is_auth and card.status in ("created", "sent", "active"):
                    card.status = "charged"
                    card.amount_charged = (
                        Decimal(str(amount)) / 100 if amount else card.amount_limit
                    )
                    card.charged_at = datetime.now(UTC)
                    card.merchant_name = merchant
                elif is_settled and card.status == "charged":
                    card.status = "completed"
                    # Create rebate
                    rebate_rate = Decimal("0.0100")  # 1% default
                    rebate_amount = (card.amount_charged or card.amount_limit) * rebate_rate
                    rebate = CardRebate(
                        virtual_card_id=card.id,
                        amount=rebate_amount,
                        rate=rebate_rate,
                        status="pending",
                        period=datetime.now(UTC).strftime("%Y-%m"),
                        organization_id=card.organization_id,
                    )
                    db.add(rebate)

                await db.commit()
                return {"received": True, "action": "updated", "card_status": card.status}

            except Exception:
                await db.rollback()
                continue

    return {"received": True, "action": "card_not_found"}


@router.get("/rebates", response_model=RebateListResponse)
async def list_rebates(
    period: str | None = None,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)),
):
    query = select(CardRebate)
    if period:
        query = query.where(CardRebate.period == period)
    query = query.order_by(CardRebate.created_at.desc())

    result = await db.execute(query)
    rebates = result.scalars().all()

    total_q = select(func.coalesce(func.sum(CardRebate.amount), 0))
    if period:
        total_q = total_q.where(CardRebate.period == period)
    total = (await db.execute(total_q)).scalar() or 0

    return RebateListResponse(
        items=[
            RebateResponse(
                id=str(r.id),
                virtual_card_id=str(r.virtual_card_id),
                amount=float(r.amount),
                rate=float(r.rate),
                status=r.status,
                period=r.period,
                created_at=r.created_at.isoformat() if r.created_at else "",
            )
            for r in rebates
        ],
        total=float(total),
    )
