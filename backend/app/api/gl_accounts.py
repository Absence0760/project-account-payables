"""GL Account (Chart of Accounts) endpoints."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    ROLE_ADMIN,
    ROLE_AP_MANAGER,
    get_current_user,
    get_org_id,
    require_roles,
)
from app.models.gl_account import GLAccount
from app.models.organization import Organization
from app.models.user import User
from app.schemas.gl_account import GLAccountCreate
from app.services.audit_dispatch import dispatch_audit
from app.tenant import apply_entity_scope, get_entity_id, get_tenant, get_tenant_db
from app.utils.search import ilike_contains

router = APIRouter(prefix="/gl-accounts", tags=["gl-accounts"])


@router.get("")
async def list_gl_accounts(
    search: str | None = None,
    account_type: str | None = None,
    active_only: bool = True,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(get_current_user),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    # A scoped chart is the shared accounts (NULL entity_id) ∪ the entity's own
    # (include_shared=True); the consolidated view (None) returns everything.
    query = apply_entity_scope(select(GLAccount), GLAccount, entity_id, include_shared=True)
    if active_only:
        query = query.where(GLAccount.is_active)
    if search:
        query = query.where(
            ilike_contains(GLAccount.code, search) | ilike_contains(GLAccount.name, search)
        )
    if account_type:
        query = query.where(GLAccount.account_type == account_type)

    query = query.order_by(GLAccount.code)
    result = await db.execute(query)
    accounts = result.scalars().all()

    return [
        {
            "id": str(a.id),
            "code": a.code,
            "name": a.name,
            "account_type": a.account_type,
            "parent_code": a.parent_code,
            "is_active": a.is_active,
            "erp_account_id": a.erp_account_id,
        }
        for a in accounts
    ]


@router.post("", status_code=201)
async def create_gl_account(
    body: GLAccountCreate,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER)),
    org_id: uuid.UUID = Depends(get_org_id),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    # Unlike other tables, a NULL entity_id is meaningful for GL: it makes the
    # account SHARED across every entity. So we deliberately use get_entity_id
    # (not get_write_entity_id) — creating an account while an entity is
    # selected makes it entity-specific; creating it in the consolidated view
    # leaves it shared (NULL). See docs/multi-entity.md § Chart of accounts.
    account = GLAccount(
        code=body.code,
        name=body.name,
        account_type=body.account_type,
        parent_code=body.parent_code,
        organization_id=org_id,
        entity_id=entity_id,
    )
    db.add(account)
    await db.flush()

    # A GL account is what invoice lines are coded to, so adding one is a
    # chart-of-accounts change that belongs on the append-only trail. PII-free:
    # code / name / type are org config. `entity_id` records whether the
    # account landed shared (NULL) or entity-specific.
    await dispatch_audit(
        db,
        correlation_id=uuid.uuid4(),
        organization_id=org_id,
        actor_id=user.id,
        action="gl_account.created",
        entity_type="gl_account",
        entity_id=account.id,
        details={
            "code": account.code,
            "name": account.name,
            "account_type": account.account_type,
            "entity_id": str(entity_id) if entity_id else None,
        },
    )
    return {
        "id": str(account.id),
        "code": account.code,
        "name": account.name,
    }


@router.post("/sync-erp")
async def sync_gl_accounts_from_erp(
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER)),
    org_id: uuid.UUID = Depends(get_org_id),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    """Pull chart of accounts from the connected ERP via its adapter.

    New accounts land shared (NULL entity_id) in the consolidated view, or
    entity-specific when an entity is selected — same rule as manual create.
    """
    erp_config = (org.settings or {}).get("erp")
    if not erp_config:
        raise HTTPException(status_code=400, detail="No ERP configured")

    # Lazy-import adapter modules so the @register_adapter decorator
    # populates the dispatcher registry. Same pattern as vendors.py
    # and purchase_orders.py.
    import app.services.erp_adapters.dynamics_365_bc  # noqa: F401
    import app.services.erp_adapters.merge_dev  # noqa: F401
    import app.services.erp_adapters.mock_adapter  # noqa: F401
    import app.services.erp_adapters.netsuite  # noqa: F401
    from app.services.erp_adapters import UnknownErpAdapterError, get_erp_adapter

    try:
        adapter = get_erp_adapter(erp_config)
    except UnknownErpAdapterError as exc:
        # A config problem, not a gateway failure — 400, not 502. Before the
        # dispatcher failed closed this resolved to `mock` and returned its
        # fixture chart of accounts as if it came from the ERP.
        raise HTTPException(
            status_code=400,
            detail=f"'{exc.adapter_key}' is not a supported ERP adapter.",
        ) from exc

    try:
        erp_accounts = await adapter.list_gl_accounts()
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"ERP request failed: {type(exc).__name__}",
        ) from exc

    created = 0
    updated = 0
    for acct in erp_accounts:
        result = await db.execute(
            select(GLAccount).where(
                GLAccount.code == acct.code,
                GLAccount.organization_id == org_id,
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            # Only count as updated when something actually changes —
            # otherwise re-running the sync would inflate the metric.
            changed = False
            if existing.name != acct.name:
                existing.name = acct.name
                changed = True
            if acct.account_type and existing.account_type != acct.account_type:
                existing.account_type = acct.account_type
                changed = True
            if acct.erp_account_id and existing.erp_account_id != acct.erp_account_id:
                existing.erp_account_id = acct.erp_account_id
                changed = True
            if changed:
                updated += 1
        else:
            db.add(
                GLAccount(
                    code=acct.code,
                    name=acct.name,
                    account_type=acct.account_type,
                    parent_code=acct.parent_code,
                    organization_id=org_id,
                    entity_id=entity_id,
                    erp_account_id=acct.erp_account_id or acct.code,
                )
            )
            created += 1

    # One PII-free summary row per sync, not one per account — the trail records
    # that a bulk chart change happened, who ran it and how much it moved.
    await dispatch_audit(
        db,
        correlation_id=uuid.uuid4(),
        organization_id=org_id,
        actor_id=user.id,
        action="gl_account.synced_from_erp",
        entity_type="gl_account",
        entity_id=org_id,
        details={
            "created": created,
            "updated": updated,
            "adapter": adapter.erp_type,
            "entity_id": str(entity_id) if entity_id else None,
        },
    )
    await db.commit()
    return {
        "success": True,
        "message": f"Synced {created} new, {updated} updated GL accounts",
        "created": created,
        "updated": updated,
        "adapter": adapter.erp_type,
    }
