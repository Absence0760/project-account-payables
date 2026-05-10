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
from app.tenant import get_tenant, get_tenant_db

router = APIRouter(prefix="/gl-accounts", tags=["gl-accounts"])


@router.get("")
async def list_gl_accounts(
    search: str | None = None,
    account_type: str | None = None,
    active_only: bool = True,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(get_current_user),
):
    query = select(GLAccount)
    if active_only:
        query = query.where(GLAccount.is_active)
    if search:
        pattern = f"%{search}%"
        query = query.where(GLAccount.code.ilike(pattern) | GLAccount.name.ilike(pattern))
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
    body: dict,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    account = GLAccount(
        code=body["code"],
        name=body["name"],
        account_type=body.get("account_type"),
        parent_code=body.get("parent_code"),
        organization_id=org_id,
    )
    db.add(account)
    await db.flush()
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
):
    """Pull chart of accounts from the connected ERP via its adapter."""
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
    from app.services.erp_adapters import get_erp_adapter

    adapter = get_erp_adapter(erp_config)
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
                    erp_account_id=acct.erp_account_id or acct.code,
                )
            )
            created += 1

    await db.commit()
    return {
        "success": True,
        "message": f"Synced {created} new, {updated} updated GL accounts",
        "created": created,
        "updated": updated,
        "adapter": adapter.erp_type,
    }
