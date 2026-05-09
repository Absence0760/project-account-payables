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
    """Pull chart of accounts from the connected ERP."""
    erp_config = (org.settings or {}).get("erp")
    if not erp_config:
        raise HTTPException(status_code=400, detail="No ERP configured")

    # TODO: call ERP adapter to fetch real chart of accounts
    # For now, use mock data
    mock_accounts = [
        {"code": "1000", "name": "Cash and Cash Equivalents", "type": "asset"},
        {"code": "1200", "name": "Accounts Receivable", "type": "asset"},
        {"code": "1500", "name": "Fixed Assets - Equipment", "type": "asset"},
        {"code": "2000", "name": "Accounts Payable", "type": "liability"},
        {"code": "2100", "name": "Accrued Liabilities", "type": "liability"},
        {"code": "3000", "name": "Owner's Equity", "type": "equity"},
        {"code": "4000", "name": "Revenue - Services", "type": "revenue"},
        {"code": "4100", "name": "Revenue - Products", "type": "revenue"},
        {"code": "6100", "name": "Office Supplies & Expenses", "type": "expense"},
        {"code": "6200", "name": "Software & Cloud Services", "type": "expense"},
        {"code": "6300", "name": "Facilities & Maintenance", "type": "expense"},
        {"code": "6400", "name": "Marketing & Advertising", "type": "expense"},
        {"code": "6500", "name": "Legal & Professional Fees", "type": "expense"},
        {"code": "6600", "name": "Meals & Entertainment", "type": "expense"},
        {"code": "6700", "name": "Shipping & Freight", "type": "expense"},
        {"code": "6800", "name": "Travel & Transportation", "type": "expense"},
        {"code": "6900", "name": "Utilities & Telecom", "type": "expense"},
        {"code": "7000", "name": "Insurance", "type": "expense"},
        {"code": "7100", "name": "Depreciation & Amortization", "type": "expense"},
        {"code": "8000", "name": "Payroll Expense", "type": "expense"},
    ]

    created = 0
    updated = 0
    for acct in mock_accounts:
        result = await db.execute(
            select(GLAccount).where(
                GLAccount.code == acct["code"],
                GLAccount.organization_id == org_id,
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            if existing.name != acct["name"]:
                existing.name = acct["name"]
                updated += 1
        else:
            db.add(
                GLAccount(
                    code=acct["code"],
                    name=acct["name"],
                    account_type=acct["type"],
                    organization_id=org_id,
                    erp_account_id=acct["code"],
                )
            )
            created += 1

    await db.commit()
    return {
        "success": True,
        "message": f"Synced {created} new, {updated} updated GL accounts",
        "created": created,
        "updated": updated,
    }
