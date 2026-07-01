"""Virtual card endpoints — generate, list, cancel, webhook."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import extract, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    ROLE_ADMIN,
    ROLE_AP_MANAGER,
    ROLE_CFO,
    get_org_id,
    require_roles,
)
from app.api.pagination import PaginationParams, pagination_params
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
from app.tenant import apply_entity_scope, get_entity_id, get_tenant, get_tenant_db

router = APIRouter(prefix="/cards", tags=["cards"])

# Platform default rebate rate when the org has no negotiated rate on file.
_DEFAULT_REBATE_RATE = Decimal("0.0100")  # 1%


def _resolve_rebate_rate(card_config: dict) -> Decimal:
    """The org's negotiated rebate rate from `settings.cards.rebate_rate`,
    falling back to the 1% platform default. Parsed defensively (a malformed
    or out-of-range value must never break payment settlement) and clamped to
    a sane 0–10% band."""
    raw = (card_config or {}).get("rebate_rate")
    if raw is None:
        return _DEFAULT_REBATE_RATE
    try:
        rate = Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError):
        return _DEFAULT_REBATE_RATE
    if rate < 0 or rate > Decimal("0.10"):
        return _DEFAULT_REBATE_RATE
    return rate


def _classify_card_event(event_type: str) -> tuple[bool, bool]:
    """Classify a card webhook `event_type` into (is_charge_auth, is_settlement).

    Provider event names are matched by substring, but the *non-charging*
    variants are excluded FIRST. Names like `authorization.decline`,
    `authorization.reversal`, or `transaction.voided` all contain
    "auth"/"transaction", so a naive substring match would treat a declined or
    reversed authorization as a real charge — flipping the card to `charged` on
    money that never moved (and later minting a rebate on it). A decline /
    reversal / void / refund / return / cancel / expiry is neither a charge nor
    a settlement: both flags are False and the handler leaves the card untouched.
    """
    et = (event_type or "").lower()
    is_decline_or_reversal = any(
        kw in et
        for kw in (
            "decline",
            "declined",
            "reversal",
            "reversed",
            "void",
            "return",
            "refund",
            "cancel",
            "expire",
        )
    )
    if is_decline_or_reversal:
        return False, False
    is_auth = "authorization" in et or "auth" in et
    is_settled = "transaction" in et or "settlement" in et
    return is_auth, is_settled


def _normalize_charge_amount(provider: str, amount, fallback: Decimal | None) -> Decimal | None:
    """Normalize a webhook charge `amount` to a major-unit Decimal.

    The unit differs by provider: Lithic webhook amounts are in MINOR units
    (cents — e.g. 150000 == $1,500.00), Nium in MAJOR units (e.g. 50.00 ==
    $50.00). Dividing both by 100 recorded 1/100th of every Nium charge (and a
    rebate on it). A falsy / unparseable amount returns `fallback` (the card's
    own limit).
    """
    if not amount:
        return fallback
    try:
        raw = Decimal(str(amount))
    except (InvalidOperation, ValueError, TypeError):
        return fallback
    return (raw / 100) if provider == "lithic" else raw


def _resolve_card_config(org: Organization) -> dict:
    """Build card adapter config based on program type.

    - "platform": use platform-level keys from app settings (you earn rebates)
    - "byok": use customer's own keys from org settings (they earn rebates)
    """
    from app.services.card_issuance import _coerce_expiry_days

    org_cards = (org.settings or {}).get("cards", {})
    program_type = org_cards.get("program_type", "platform")
    region = org_cards.get("region", "US")
    expiry_days = _coerce_expiry_days(org_cards.get("default_expiry_days"))

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
            # BYOK sandbox is opt-IN: a customer who wires their own real
            # provider keys expects live rails. Defaulting to sandbox silently
            # routed their production key at the sandbox host (invoices paid
            # into a void). Sandbox must be an explicit `"sandbox": true`.
            "sandbox": org_cards.get("sandbox", False),
            "default_expiry_days": expiry_days,
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
                "default_expiry_days": expiry_days,
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
                "default_expiry_days": expiry_days,
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
        amount_limit=card.amount_limit,
        amount_charged=card.amount_charged,
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
    pagination: PaginationParams = Depends(pagination_params),
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    query = apply_entity_scope(
        select(VirtualCard, Invoice).outerjoin(Invoice, VirtualCard.invoice_id == Invoice.id),
        VirtualCard,
        entity_id,
    )
    if status_filter:
        statuses = [s.strip() for s in status_filter.split(",")]
        query = query.where(VirtualCard.status.in_(statuses))

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar() or 0

    query = (
        query.order_by(VirtualCard.created_at.desc())
        .offset(pagination.offset)
        .limit(pagination.limit)
    )
    result = await db.execute(query)
    rows = result.all()

    return CardListResponse(
        items=[_card_response(card, inv) for card, inv in rows],
        total=int(total),
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.get("/dashboard", response_model=CardDashboardResponse)
async def card_dashboard(
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    now = datetime.now(UTC)

    # Active cards (scoped to the entity; rebates below are control-plane and
    # stay org-wide, like the payments summary).
    active_q = apply_entity_scope(
        select(func.count(), func.coalesce(func.sum(VirtualCard.amount_limit), 0)).where(
            VirtualCard.status.in_(["created", "sent", "active"])
        ),
        VirtualCard,
        entity_id,
    )
    active_result = await db.execute(active_q)
    active_count, active_value = active_result.one()

    # Spend this month
    spend_q = apply_entity_scope(
        select(func.coalesce(func.sum(VirtualCard.amount_charged), 0)).where(
            VirtualCard.status.in_(["charged", "completed"]),
            extract("month", VirtualCard.charged_at) == now.month,
            extract("year", VirtualCard.charged_at) == now.year,
        ),
        VirtualCard,
        entity_id,
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

    # Projected annual: (YTD / months elapsed) × 12. In January where
    # YTD is short, the per-day rate is too noisy, so we fall back to
    # rebates_this_month × 12 if we haven't accrued any YTD yet.
    # Decimal throughout (money is exact). The aggregates above are already
    # Decimal (sum over Numeric columns); keep them so and quantize the
    # projection to cents rather than hopping through float.
    months_elapsed = now.month
    if rebate_ytd:
        projected_annual = Decimal(rebate_ytd) / months_elapsed * 12
    else:
        projected_annual = Decimal(rebate_month) * 12
    projected_annual = projected_annual.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    return CardDashboardResponse(
        active_cards=active_count or 0,
        active_cards_value=active_value,
        spend_this_month=spend_this_month,
        rebates_this_month=rebate_month,
        rebates_ytd=rebate_ytd,
        projected_annual_rebates=projected_annual,
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
    from app.services.card_issuance import DEFAULT_CARD_EXPIRY_DAYS

    adapter = get_card_adapter(card_config)
    expiry_days = card_config.get("default_expiry_days", DEFAULT_CARD_EXPIRY_DAYS)

    # Load invoices
    ids = [uuid.UUID(i) for i in body.invoice_ids]
    result = await db.execute(select(Invoice).where(Invoice.id.in_(ids)))
    invoices = result.scalars().all()

    # Idempotency: skip invoices that already have a LIVE (non-cancelled) card so
    # a retried request (network timeout, double-click) doesn't mint a second
    # provider card. The partial unique index uq_virtual_cards_one_live_per_invoice
    # is the hard backstop against a concurrent race; this pre-check avoids the
    # wasted provider call on the common sequential-retry case.
    already_carded = set(
        (
            await db.execute(
                select(VirtualCard.invoice_id).where(
                    VirtualCard.invoice_id.in_(ids),
                    VirtualCard.status != "cancelled",
                )
            )
        )
        .scalars()
        .all()
    )

    cards: list[VirtualCard] = []
    for inv in invoices:
        if inv.id in already_carded:
            continue
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
            entity_id=inv.entity_id,  # card follows the invoice it pays (P2)
        )
        db.add(card)
        # Flush inside a savepoint so a concurrent duplicate (caught by the
        # partial unique index) skips just that card instead of aborting the
        # whole batch and orphaning the other freshly-minted provider cards.
        try:
            async with db.begin_nested():
                await db.flush()
        except IntegrityError:
            already_carded.add(inv.id)
            continue
        cards.append(card)

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
    # Cancel at the provider FIRST, then reflect it in the DB — never the other
    # way round. The fail-safe direction is "dead at the provider, maybe stale
    # in the DB"; the dangerous direction is a card the AP team believes is
    # cancelled while it is still chargeable at the provider. So we only mark
    # the row cancelled once the provider CONFIRMS the close.
    try:
        cancelled_ok = await adapter.cancel_card(card.provider_card_id)
    except Exception as exc:  # noqa: BLE001
        # Provider unreachable — its state is unknown. Don't record an
        # unverified cancel; let the client retry. (PII guard: type only.)
        import logging

        logging.getLogger(__name__).warning(
            "[cards] provider cancel raised for card %s: %s", card.id, exc.__class__.__name__
        )
        raise HTTPException(
            status_code=502, detail="Card provider is unavailable; please retry."
        ) from None
    if not cancelled_ok:
        raise HTTPException(
            status_code=502,
            detail="Card provider did not confirm cancellation; please retry.",
        )
    prior_status = card.status
    card.status = "cancelled"

    # Cancelling voids an issued card before it can be charged — a card
    # lifecycle state change must leave an append-only audit row (project
    # invariant: status transitions write audit). PII-free: only the
    # last_four + the from/to status, never the PAN.
    from app.services.audit_dispatch import dispatch_audit

    await dispatch_audit(
        db,
        correlation_id=card.correlation_id or uuid.uuid4(),
        organization_id=card.organization_id,
        actor_id=user.id,
        action="card.cancelled",
        entity_type="virtual_card",
        entity_id=card.id,
        details={"last_four": card.last_four, "from": prior_status, "to": "cancelled"},
    )

    await db.commit()

    return {"success": True, "message": "Card cancelled"}


@router.post("/webhook/{provider}", status_code=status.HTTP_204_NO_CONTENT)
async def card_webhook(provider: str, request: Request):
    """Handle charge/settlement webhooks from card providers.

    Authenticated by HMAC over the raw body. The signing secret is
    looked up off the tenant whose card the event references — we
    can't pick the right secret without first identifying the
    tenant, so the flow is:

      1. Parse the body for `card_token` + `event_id`
      2. Find the owning tenant by `card_token`
      3. Verify HMAC against that tenant's `cards.webhook_signing_secret`
      4. Dedupe by event id (cross-tenant key) so re-delivery is a
         silent no-op
      5. Apply the state change inside a row-locked transaction

    Bad signatures, unknown card tokens, missing event ids — every
    failure returns 204 silently. Leaking the difference would help
    an attacker probe for valid tokens.
    """
    raw_body = await request.body()
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return  # malformed JSON → silent 204

    # Normalize by provider
    if provider == "lithic":
        card_token = body.get("card_token") or body.get("card", {}).get("token")
        event_type = body.get("type", "")
        event_id = body.get("event_id") or body.get("token") or body.get("id")
        amount = body.get("amount", 0)
        merchant = body.get("merchant", {}).get("descriptor")
    elif provider == "nium":
        card_token = body.get("cardHashId")
        event_type = body.get("eventType", "")
        event_id = body.get("webhookId") or body.get("eventId") or body.get("id")
        amount = body.get("amount", 0)
        merchant = body.get("merchantName")
    else:
        return

    if not card_token:
        return

    # Find the card across tenant DBs. The lookup is fail-soft —
    # if the card isn't anywhere, we return 204 silently rather than
    # confirming "no such card" (enumeration vector).
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.database import control_session_factory, get_tenant_engine
    from app.services.webhook_security import (
        extract_signature_header,
        is_event_already_processed,
        release_event_claim,
        verify_hmac_sha256,
    )

    async with control_session_factory() as ctrl_db:
        orgs_result = await ctrl_db.execute(select(Organization))
        orgs = orgs_result.scalars().all()

    for org_obj in orgs:
        engine = get_tenant_engine(org_obj.db_name)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            claimed_event: str | None = None
            try:
                # Row-lock the card so two concurrent deliveries of events for
                # the SAME card serialize at the DB layer (one waits for the
                # other's commit/rollback) — the Redis dedup alone can't order
                # them if both slip through the NX gap under load.
                result = await db.execute(
                    select(VirtualCard)
                    .where(VirtualCard.provider_card_id == card_token)
                    .with_for_update()
                )
                card = result.scalar_one_or_none()
                if not card:
                    continue

                # We've identified the owning tenant. Verify HMAC
                # against that tenant's signing secret before doing
                # anything else.
                card_config = (org_obj.settings or {}).get("cards") or {}
                signing_secret = card_config.get("webhook_signing_secret", "")
                provided_sig = extract_signature_header(
                    dict(request.headers),
                    "Webhook-Signature",
                    "X-Webhook-Signature",
                    "X-Signature",
                )
                if not verify_hmac_sha256(signing_secret, raw_body, provided_sig):
                    return  # silent 204 on bad / missing signature

                # Dedup: if this event id has been processed already
                # (across any tenant), short-circuit. Same response
                # as first delivery so the provider doesn't retry.
                if await is_event_already_processed(provider, str(event_id or "")):
                    return
                # Track the claim so we can release it if the commit below
                # fails — the Redis claim is only durable AFTER the DB change
                # commits; otherwise a rolled-back txn would strand the event
                # id as "processed" and the retry would be deduped away.
                claimed_event = str(event_id) if event_id else None

                # Update card based on event (declines / reversals excluded).
                is_auth, is_settled = _classify_card_event(event_type)

                from app.services.audit_dispatch import dispatch_audit

                if is_auth and card.status in ("created", "sent", "active"):
                    prior_status = card.status
                    card.status = "charged"
                    card.amount_charged = _normalize_charge_amount(
                        provider, amount, card.amount_limit
                    )
                    card.charged_at = datetime.now(UTC)
                    card.merchant_name = merchant
                    # A charge is a money-state transition — leave an
                    # append-only audit row. Amount serialises as a string
                    # Decimal (never float); no PAN ever enters the trail.
                    await dispatch_audit(
                        db,
                        correlation_id=card.correlation_id or uuid.uuid4(),
                        organization_id=card.organization_id,
                        actor_id=None,
                        action="card.charged",
                        entity_type="virtual_card",
                        entity_id=card.id,
                        details={
                            "last_four": card.last_four,
                            "from": prior_status,
                            "to": "charged",
                            "amount_charged": str(card.amount_charged),
                        },
                    )
                elif is_settled and card.status == "charged":
                    card.status = "completed"
                    # Create rebate at the org's negotiated rate (not a hardcoded
                    # 1%), quantized to cents — the rate field was documented on
                    # settings.cards but never read, so every org earned 1%.
                    rebate_rate = _resolve_rebate_rate(card_config)
                    _rebate_base = card.amount_charged or card.amount_limit
                    rebate_amount = (_rebate_base * rebate_rate).quantize(
                        Decimal("0.01"), rounding=ROUND_HALF_UP
                    )
                    rebate = CardRebate(
                        virtual_card_id=card.id,
                        amount=rebate_amount,
                        rate=rebate_rate,
                        status="pending",
                        period=datetime.now(UTC).strftime("%Y-%m"),
                        organization_id=card.organization_id,
                    )
                    db.add(rebate)
                    await dispatch_audit(
                        db,
                        correlation_id=card.correlation_id or uuid.uuid4(),
                        organization_id=card.organization_id,
                        actor_id=None,
                        action="card.settled",
                        entity_type="virtual_card",
                        entity_id=card.id,
                        details={
                            "last_four": card.last_four,
                            "from": "charged",
                            "to": "completed",
                            "rebate_amount": str(rebate_amount),
                            "rebate_rate": str(rebate_rate),
                        },
                    )

                await db.commit()
                return

            except Exception:
                await db.rollback()
                # The dedup claim guards a side effect that just rolled back —
                # release it so the provider's retry can reprocess (otherwise
                # the rebate / charge is dropped for the full TTL window).
                if claimed_event is not None:
                    await release_event_claim(provider, claimed_event)
                continue

    return


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
                amount=r.amount,
                rate=r.rate,
                status=r.status,
                period=r.period,
                created_at=r.created_at.isoformat() if r.created_at else "",
            )
            for r in rebates
        ],
        total=total,
    )
