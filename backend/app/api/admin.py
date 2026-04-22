"""User management endpoints for organization admins."""

import secrets
import string
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import ROLE_ADMIN, get_org_id, require_roles
from app.database import get_control_db
from app.models.user import Role, User, UserRole
from app.schemas.admin import (
    AdminUserListResponse,
    AdminUserResponse,
    CreateUserRequest,
    CreateUserResponse,
    RoleResponse,
    UpdateUserRequest,
)
from app.services.session_management import revoke_user_sessions

router = APIRouter(prefix="/admin", tags=["admin"])
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _generate_temp_password(length: int = 12) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _user_to_response(user: User) -> AdminUserResponse:
    return AdminUserResponse(
        id=str(user.id),
        email=user.email,
        full_name=user.full_name,
        is_active=user.is_active,
        roles=[
            RoleResponse(id=str(r.id), name=r.name, description=r.description) for r in user.roles
        ],
        created_at=user.created_at.isoformat() if user.created_at else "",
    )


@router.get("/users", response_model=AdminUserListResponse)
async def list_users(
    db: AsyncSession = Depends(get_control_db),
    user: User = Depends(require_roles(ROLE_ADMIN)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    result = await db.execute(
        select(User)
        .where(User.organization_id == org_id)
        .options(selectinload(User.roles))
        .order_by(User.created_at.desc())
    )
    users = result.scalars().all()
    return AdminUserListResponse(
        items=[_user_to_response(u) for u in users],
        total=len(users),
    )


@router.get("/roles", response_model=list[RoleResponse])
async def list_roles(
    db: AsyncSession = Depends(get_control_db),
    user: User = Depends(require_roles(ROLE_ADMIN)),
):
    result = await db.execute(select(Role).order_by(Role.name))
    roles = result.scalars().all()
    return [RoleResponse(id=str(r.id), name=r.name, description=r.description) for r in roles]


@router.post("/users", response_model=CreateUserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: CreateUserRequest,
    db: AsyncSession = Depends(get_control_db),
    user: User = Depends(require_roles(ROLE_ADMIN)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    # Check email uniqueness
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Email already in use")

    temp_password = _generate_temp_password()
    new_user = User(
        email=body.email,
        full_name=body.full_name,
        hashed_password=pwd_context.hash(temp_password),
        organization_id=org_id,
    )
    db.add(new_user)
    await db.flush()

    # Assign roles
    if body.role_names:
        result = await db.execute(select(Role).where(Role.name.in_(body.role_names)))
        roles = result.scalars().all()
        for role in roles:
            db.add(UserRole(user_id=new_user.id, role_id=role.id))
        await db.flush()

    # Reload with roles
    result = await db.execute(
        select(User).where(User.id == new_user.id).options(selectinload(User.roles))
    )
    new_user = result.scalar_one()
    await db.commit()

    resp = _user_to_response(new_user)
    return CreateUserResponse(**resp.model_dump(), temporary_password=temp_password)


@router.patch("/users/{user_id}", response_model=AdminUserResponse)
async def update_user(
    user_id: uuid.UUID,
    body: UpdateUserRequest,
    db: AsyncSession = Depends(get_control_db),
    current_user: User = Depends(require_roles(ROLE_ADMIN)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    result = await db.execute(
        select(User)
        .where(User.id == user_id, User.organization_id == org_id)
        .options(selectinload(User.roles))
    )
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    # Snapshot pre-state so we can decide whether to force-logout the target.
    previous_role_names = sorted(r.name for r in target.roles)
    was_active = target.is_active

    if body.full_name is not None:
        target.full_name = body.full_name
    if body.email is not None:
        existing = await db.execute(
            select(User).where(User.email == body.email, User.id != user_id)
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Email already in use")
        target.email = body.email
    if body.is_active is not None:
        target.is_active = body.is_active
    if body.password is not None:
        target.hashed_password = pwd_context.hash(body.password)

    roles_changed = False
    if body.role_names is not None:
        new_role_names = sorted(set(body.role_names))
        roles_changed = new_role_names != previous_role_names
        await db.execute(UserRole.__table__.delete().where(UserRole.user_id == user_id))
        if body.role_names:
            result = await db.execute(select(Role).where(Role.name.in_(body.role_names)))
            roles = result.scalars().all()
            for role in roles:
                db.add(UserRole(user_id=user_id, role_id=role.id))

    await db.flush()

    result = await db.execute(
        select(User).where(User.id == user_id).options(selectinload(User.roles))
    )
    target = result.scalar_one()
    await db.commit()

    # SOC 2: a role change (elevation or demotion) and account deactivation
    # must both drop the user's existing sessions. The previous JWT was signed
    # before the permission change and would otherwise keep the old role set
    # alive until it expired (up to 30 min).
    deactivated = was_active and body.is_active is False
    if roles_changed or deactivated:
        await revoke_user_sessions(user_id)

    return _user_to_response(target)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_control_db),
    current_user: User = Depends(require_roles(ROLE_ADMIN)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    result = await db.execute(
        select(User).where(User.id == user_id, User.organization_id == org_id)
    )
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if target.id == current_user.id:
        raise HTTPException(status_code=409, detail="Cannot delete yourself")

    # Remove role assignments first
    await db.execute(UserRole.__table__.delete().where(UserRole.user_id == user_id))
    await db.delete(target)
    await db.commit()

    # Deletion → revoke any active JWT so the tombstoned user can't keep
    # hitting the API until their token expires.
    await revoke_user_sessions(user_id)
