"""Customer-facing billing — current plan, status, usage-to-date (`/api/billing`).

FIRST SLICE: a single read endpoint, ``GET /api/billing/subscription``. It
returns the requesting tenant's live plan + subscription status + a usage rollup
for the current period. Plan-change, payment-method, and invoice-list endpoints
are later slices.

Auth-before-everything: behind JWT + ``require_roles(admin, cfo)`` (billing is a
finance/admin concern). The org is resolved from the tenant chokepoint
(``get_tenant``), and the usage rollup reads the CONTROL-PLANE usage tables off
``get_control_db``. Money is serialised as exact decimal strings (never float) —
this is a billing surface where exactness is the point. See
``backend/docs/billing.md``.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ROLE_ADMIN, ROLE_CFO, require_roles
from app.config import settings
from app.database import get_control_db
from app.models.billing import Plan
from app.models.organization import Organization
from app.models.user import User
from app.services.billing import (
    PlanChangeError,
    change_plan,
    current_period,
    get_active_subscription,
    rollup_usage,
)
from app.services.billing_adapters import get_billing_adapter
from app.tenant import get_tenant, get_tenant_db

router = APIRouter(prefix="/billing", tags=["billing"])


class PlanView(BaseModel):
    code: str
    name: str
    # Exact money as a decimal string — never float on a billing surface.
    monthly_price: str
    currency: str
    entitlements: dict
    trial_days: int


class SubscriptionView(BaseModel):
    status: str
    current_period_start: datetime | None
    current_period_end: datetime | None
    trial_end: datetime | None
    # Whether a live provider subscription backs this (always False on mock).
    externally_managed: bool


class BillingSummaryResponse(BaseModel):
    # Provider in effect for this org (per-org override → FEOH_BILLING_PROVIDER).
    provider: str
    # None when the org has no live subscription (e.g. never subscribed).
    plan: PlanView | None
    subscription: SubscriptionView | None
    period: str
    # Billable meters for the current period, as exact decimal strings.
    usage: dict[str, str]


def _current_period() -> str:
    return datetime.now(UTC).strftime("%Y-%m")


def _resolve_provider(org: Organization) -> str:
    """Per-org override (`settings.billing.provider`) → platform default."""
    billing = (org.settings or {}).get("billing") or {}
    return billing.get("provider") or settings.billing_provider


@router.get("/subscription", response_model=BillingSummaryResponse)
async def get_subscription(
    org: Organization = Depends(get_tenant),
    _user: User = Depends(require_roles(ROLE_ADMIN, ROLE_CFO)),
    control_db: AsyncSession = Depends(get_control_db),
    tenant_db: AsyncSession = Depends(get_tenant_db),
) -> BillingSummaryResponse:
    """Current plan + subscription status + usage-to-date for the tenant.

    Plans/subscriptions are control-plane (`control_db`); the usage meters
    (`extraction_usage` / `card_rebates`) are tenant-scoped, so the rollup reads
    `tenant_db`.
    """
    period = _current_period()
    active = await get_active_subscription(control_db, org.id)

    plan_view: PlanView | None = None
    sub_view: SubscriptionView | None = None
    if active is not None:
        subscription, plan = active
        plan_view = PlanView(
            code=plan.code,
            name=plan.name,
            monthly_price=str(plan.monthly_price),
            currency=plan.currency,
            entitlements=dict(plan.entitlements or {}),
            trial_days=plan.trial_days,
        )
        # Report the window the subscription is actually in, resolved by the
        # same rule `change_plan` persists and the dunning grace clock reads
        # (`services/billing/period.py`). Compute-on-read, no write: a row
        # created before that rule existed carries NULL bounds, and echoing
        # those told the customer their billing period was unknown while the
        # plan-change screen quietly prorated `0.00` off the same absence.
        window = current_period(subscription, now=datetime.now(UTC))
        sub_view = SubscriptionView(
            status=subscription.status,
            current_period_start=window.start,
            current_period_end=window.end,
            trial_end=subscription.trial_end,
            externally_managed=subscription.external_subscription_id is not None,
        )

    usage = await rollup_usage(tenant_db, organization_id=org.id, period=period)

    return BillingSummaryResponse(
        provider=_resolve_provider(org),
        plan=plan_view,
        subscription=sub_view,
        period=period,
        usage=usage.as_meters(),
    )


class PlansCatalogResponse(BaseModel):
    plans: list[PlanView]


@router.get("/plans", response_model=PlansCatalogResponse)
async def list_plans(
    _user: User = Depends(require_roles(ROLE_ADMIN, ROLE_CFO)),
    control_db: AsyncSession = Depends(get_control_db),
) -> PlansCatalogResponse:
    """The sellable plan catalog (active plans only), for the plan-change picker.

    admin/cfo only (matches the other billing routes). The catalog is global —
    not org-scoped — so this doesn't resolve a tenant, only the caller's
    control-plane identity/role. Ordered by price so the picker reads as a
    ladder. Money is an exact decimal string, same as every other billing view.
    """
    plans = (
        (
            await control_db.execute(
                select(Plan).where(Plan.is_active.is_(True)).order_by(Plan.monthly_price)
            )
        )
        .scalars()
        .all()
    )
    return PlansCatalogResponse(
        plans=[
            PlanView(
                code=p.code,
                name=p.name,
                monthly_price=str(p.monthly_price),
                currency=p.currency,
                entitlements=dict(p.entitlements or {}),
                trial_days=p.trial_days,
            )
            for p in plans
        ]
    )


class PlanChangeRequest(BaseModel):
    # Target plan's stable machine code (Plan.code).
    plan_code: str = Field(min_length=1, max_length=50)


class ProrationView(BaseModel):
    # Net mid-period adjustment as an exact decimal STRING (never float):
    # positive = extra charge (upgrade), negative = credit (downgrade),
    # "0.00" = no change / same plan.
    amount: str
    unused_days: int
    period_days: int


class PlanChangeResponse(BaseModel):
    changed: bool
    old_plan_code: str
    new_plan_code: str
    proration: ProrationView


@router.post("/change-plan", response_model=PlanChangeResponse)
async def change_subscription_plan(
    body: PlanChangeRequest,
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_CFO)),
    control_db: AsyncSession = Depends(get_control_db),
) -> PlanChangeResponse:
    """Move the org's live subscription to another plan, prorated mid-period.

    admin/cfo only (matches the read endpoint). Idempotent: changing to the plan
    the org is already on is a successful no-op (``changed=false``, zero
    proration) — a retry can't double-charge. 404 when the org has no live
    subscription or the target plan is unknown/inactive. Every applied change
    writes an append-only ``billing.plan_changed`` audit row. Money in the
    response is an exact decimal string.
    """
    try:
        result = await change_plan(
            control_db, org=org, new_plan_code=body.plan_code, actor_id=user.id
        )
    except PlanChangeError as exc:
        # 404 (not 400): don't enumerate which plan codes / subscriptions exist.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Plan change not available."
        ) from exc

    return PlanChangeResponse(
        changed=result.changed,
        old_plan_code=result.old_plan_code,
        new_plan_code=result.new_plan_code,
        proration=ProrationView(
            amount=str(result.proration.amount),
            unused_days=result.proration.unused_days,
            period_days=result.proration.period_days,
        ),
    )


class BillingInvoiceView(BaseModel):
    id: str
    number: str | None
    period: str | None
    # Exact money as a decimal string — never float on a billing surface.
    amount: str
    currency: str
    status: str  # paid | open | void
    hosted_url: str | None
    created_at: str | None


class BillingInvoicesResponse(BaseModel):
    provider: str
    invoices: list[BillingInvoiceView]


def _resolve_customer_id(org: Organization) -> str | None:
    """The provider-side customer id persisted on `settings.billing`, if any.

    `None` when the org was never provisioned with the billing provider — the
    adapter then has nothing to list and returns an empty list (not a 500).
    """
    billing = (org.settings or {}).get("billing") or {}
    return billing.get("stripe_customer_id")


@router.get("/invoices", response_model=BillingInvoicesResponse)
async def list_billing_invoices(
    org: Organization = Depends(get_tenant),
    _user: User = Depends(require_roles(ROLE_ADMIN, ROLE_CFO)),
) -> BillingInvoicesResponse:
    """The org's past platform-billing invoices / receipts (newest first).

    admin/cfo only (matches `GET /subscription`). Sourced through the org's
    billing adapter (`mock` locally → deterministic synthetic receipts;
    `stripe_billing` → the org's Stripe invoices). Graceful degradation: an org
    never provisioned with the provider (no customer id) — or an unconfigured /
    unavailable provider — yields an empty list, never a 500. Money is an exact
    decimal string (this is a billing surface — exactness is the point).
    """
    provider = _resolve_provider(org)
    adapter = get_billing_adapter(provider)
    customer_id = _resolve_customer_id(org)
    try:
        provider_invoices = await adapter.list_invoices(customer_id=customer_id)
    except Exception:  # noqa: BLE001
        # The live provider fails closed (no key) or is unreachable — surface an
        # empty list rather than a 500. PII-free: no provider detail is echoed.
        provider_invoices = []

    return BillingInvoicesResponse(
        provider=provider,
        invoices=[
            BillingInvoiceView(
                id=inv.external_invoice_id,
                number=inv.number,
                period=inv.period,
                amount=inv.amount,
                currency=inv.currency,
                status=inv.status,
                hosted_url=inv.hosted_url,
                created_at=inv.created_at,
            )
            for inv in provider_invoices
        ],
    )


class SetupIntentResponse(BaseModel):
    provider: str
    # True once a SetupIntent could be started (org provisioned + provider
    # configured). False → `client_secret` is None and the UI shows a clear
    # "billing not configured" state rather than an error.
    configured: bool
    # Single-use secret the frontend confirms the card with (via the provider's
    # JS SDK). None when not configured. NEVER a long-lived secret or a PAN.
    client_secret: str | None
    setup_intent_id: str | None


@router.post("/payment-method/setup-intent", response_model=SetupIntentResponse)
async def create_payment_method_setup_intent(
    org: Organization = Depends(get_tenant),
    _user: User = Depends(require_roles(ROLE_ADMIN, ROLE_CFO)),
) -> SetupIntentResponse:
    """Start a SetupIntent so the org can add or replace a saved card.

    admin/cfo only (matches the other billing routes). Returns the provider's
    single-use `client_secret`; the frontend confirms the card against it with
    the provider's JS SDK — no charge, no PAN ever touches our backend. Graceful
    degradation: an org never provisioned with the provider (no customer id) — or
    an unconfigured / unavailable provider — yields `configured=false` with a
    null `client_secret`, never a 500.
    """
    provider = _resolve_provider(org)
    adapter = get_billing_adapter(provider)
    customer_id = _resolve_customer_id(org)
    try:
        intent = await adapter.create_setup_intent(customer_id)
    except Exception:  # noqa: BLE001
        # The live provider fails closed (no key) or is unreachable — surface a
        # not-configured shape rather than a 500. PII-free: no provider detail.
        intent = None

    return SetupIntentResponse(
        provider=provider,
        configured=intent is not None,
        client_secret=intent.client_secret if intent else None,
        setup_intent_id=intent.external_setup_intent_id if intent else None,
    )


class PaymentMethodView(BaseModel):
    id: str
    # PII-safe card metadata ONLY — brand / last4 / expiry. NEVER a full PAN.
    brand: str | None
    last4: str | None
    exp_month: int | None
    exp_year: int | None
    is_default: bool


class PaymentMethodsResponse(BaseModel):
    provider: str
    payment_methods: list[PaymentMethodView]


@router.get("/payment-methods", response_model=PaymentMethodsResponse)
async def list_payment_methods(
    org: Organization = Depends(get_tenant),
    _user: User = Depends(require_roles(ROLE_ADMIN, ROLE_CFO)),
) -> PaymentMethodsResponse:
    """The org's saved cards — PII-safe metadata only (brand / last4 / expiry).

    admin/cfo only. NEVER returns or logs a full card number. Graceful
    degradation: an org never provisioned with the provider (no customer id) — or
    an unconfigured / unavailable provider — yields an empty list, never a 500.
    """
    provider = _resolve_provider(org)
    adapter = get_billing_adapter(provider)
    customer_id = _resolve_customer_id(org)
    try:
        methods = await adapter.list_payment_methods(customer_id)
    except Exception:  # noqa: BLE001
        methods = []

    return PaymentMethodsResponse(
        provider=provider,
        payment_methods=[
            PaymentMethodView(
                id=pm.external_payment_method_id,
                brand=pm.brand,
                last4=pm.last4,
                exp_month=pm.exp_month,
                exp_year=pm.exp_year,
                is_default=pm.is_default,
            )
            for pm in methods
        ],
    )
