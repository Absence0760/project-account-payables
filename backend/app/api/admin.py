"""User management endpoints for organization admins."""

import secrets
import string
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.api.deps import (
    ALL_ROLES,
    ROLE_ADMIN,
    get_org_id,
    require_permission,
    require_roles,
)
from app.api.pagination import PaginationParams, pagination_params
from app.api.permissions import (
    ALL_PERMISSIONS,
    PERM_USER_MANAGE,
    PERMISSION_LABELS,
    effective_permissions,
    permissions_for_role,
    sanitize_permissions,
)
from app.database import get_control_db, get_tenant_engine
from app.models.organization import Organization
from app.models.user import Role, User, UserRole
from app.schemas.admin import (
    AdminUserListResponse,
    AdminUserResponse,
    CreateRoleRequest,
    CreateUserRequest,
    CreateUserResponse,
    PermissionCatalogEntry,
    RoleResponse,
    UpdateRoleRequest,
    UpdateUserRequest,
)
from app.services.session_management import revoke_user_sessions
from app.utils.passwords import PasswordError, pwd_context, validate_password_complexity

router = APIRouter(prefix="/admin", tags=["admin"])


def _generate_temp_password(length: int = 12) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _role_to_response(role: Role) -> RoleResponse:
    # System role → its static default permission set; custom role → its stored
    # (sanitized) list. `effective_permissions` over a single role yields the
    # same union, so it's the one place this resolution lives.
    perms = effective_permissions([role])
    return RoleResponse(
        id=str(role.id),
        name=role.name,
        description=role.description,
        is_system=role.organization_id is None,
        # Sort by catalog order for a stable, predictable UI.
        permissions=[p for p in ALL_PERMISSIONS if p in perms],
    )


def _user_to_response(user: User) -> AdminUserResponse:
    return AdminUserResponse(
        id=str(user.id),
        email=user.email,
        full_name=user.full_name,
        is_active=user.is_active,
        roles=[_role_to_response(r) for r in user.roles],
        created_at=user.created_at.isoformat() if user.created_at else "",
    )


@router.get("/users", response_model=AdminUserListResponse)
async def list_users(
    db: AsyncSession = Depends(get_control_db),
    user: User = Depends(require_roles(ROLE_ADMIN)),
    org_id: uuid.UUID = Depends(get_org_id),
    search: str | None = Query(None, description="Filter by full_name or email (case-insensitive)"),
    pagination: PaginationParams = Depends(pagination_params),
):
    base = select(User).where(User.organization_id == org_id)
    if search and search.strip():
        like = f"%{search.strip().lower()}%"
        base = base.where((User.full_name.ilike(like)) | (User.email.ilike(like)))

    total_q = await db.execute(select(func.count()).select_from(base.subquery()))
    total = int(total_q.scalar() or 0)

    paged = (
        base.options(selectinload(User.roles))
        .order_by(User.created_at.desc())
        .offset(pagination.offset)
        .limit(pagination.limit)
    )
    result = await db.execute(paged)
    users = result.scalars().all()
    return AdminUserListResponse(
        items=[_user_to_response(u) for u in users],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.get("/roles", response_model=list[RoleResponse])
async def list_roles(
    db: AsyncSession = Depends(get_control_db),
    user: User = Depends(require_roles(ROLE_ADMIN)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    """System roles + this org's custom roles. The four built-ins
    (organization_id IS NULL) gate hardcoded routes; custom rows are
    org-scoped."""
    result = await db.execute(
        select(Role)
        .where((Role.organization_id.is_(None)) | (Role.organization_id == org_id))
        .order_by(Role.organization_id.is_not(None), Role.name)
    )
    return [_role_to_response(r) for r in result.scalars().all()]


@router.get("/permissions", response_model=list[PermissionCatalogEntry])
async def list_permission_catalog(
    user: User = Depends(require_roles(ROLE_ADMIN)),
):
    """The granular-permission catalog (key + human label), in display order.

    Drives the permission checkboxes in the /admin/roles create/edit modal so
    the frontend never hardcodes the catalog. Admin-only (it's the role editor's
    companion); it's static data, no DB read."""
    return [
        PermissionCatalogEntry(key=key, label=PERMISSION_LABELS[key]) for key in ALL_PERMISSIONS
    ]


@router.post("/roles", response_model=RoleResponse, status_code=status.HTTP_201_CREATED)
async def create_role(
    body: CreateRoleRequest,
    db: AsyncSession = Depends(get_control_db),
    user: User = Depends(require_roles(ROLE_ADMIN)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    """Mint a custom role for this org. The name must not collide with a
    system role, since the route-level RBAC gates would silently treat the
    custom role as the built-in one.

    A custom role grants exactly the granular permissions in `body.permissions`
    (sanitized to the catalog). With an empty list it's an inert organizational
    label, exactly as before this layer; with permissions it lets the org SPLIT
    fraud-sensitive duties (e.g. approve invoices but not execute payments). See
    roadmap "Granular permissions / segregation of duties" + docs/authentication.md
    § RBAC."""
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Role name cannot be empty")
    if name in ALL_ROLES:
        raise HTTPException(status_code=400, detail=f"'{name}' is a reserved system role name")
    # Per-org uniqueness on (name, organization_id) — DB enforces it via
    # uq_roles_name_org, but we 409 explicitly so the UI can show a clean
    # message instead of an IntegrityError surfaced as 500.
    existing = await db.execute(
        select(Role).where(Role.name == name, Role.organization_id == org_id)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Role name already exists for this org")

    role = Role(
        name=name,
        description=body.description,
        organization_id=org_id,
        permissions=sanitize_permissions(body.permissions),
    )
    db.add(role)
    await db.commit()
    await db.refresh(role)
    return _role_to_response(role)


@router.patch("/roles/{role_id}", response_model=RoleResponse)
async def update_role(
    role_id: uuid.UUID,
    body: UpdateRoleRequest,
    db: AsyncSession = Depends(get_control_db),
    user: User = Depends(require_roles(ROLE_ADMIN)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    """Edit a custom role — its description and/or granular permissions. The
    name is immutable (it's referenced by approval-chain configs) and system
    roles are read-only entirely (renaming `admin` would silently break every
    `require_roles(ROLE_ADMIN)` gate; their permissions come from the static
    default map, not this column)."""
    role = (await db.execute(select(Role).where(Role.id == role_id))).scalar_one_or_none()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    if role.organization_id is None:
        raise HTTPException(status_code=403, detail="System roles cannot be edited")
    if role.organization_id != org_id:
        raise HTTPException(status_code=404, detail="Role not found")

    if body.description is not None:
        role.description = body.description
    # A provided list (even empty) replaces the grants; omitting it (None)
    # leaves them untouched. Sanitized to the known catalog.
    if body.permissions is not None:
        role.permissions = sanitize_permissions(body.permissions)
    await db.commit()
    await db.refresh(role)
    return _role_to_response(role)


@router.delete("/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_role(
    role_id: uuid.UUID,
    db: AsyncSession = Depends(get_control_db),
    user: User = Depends(require_roles(ROLE_ADMIN)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    """Delete a custom role. Refuses if any user still holds it (409 with
    `users_count`) so the operator can detach it first instead of leaving
    orphaned UserRole rows. System roles are protected."""
    role = (await db.execute(select(Role).where(Role.id == role_id))).scalar_one_or_none()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    if role.organization_id is None:
        raise HTTPException(status_code=403, detail="System roles cannot be deleted")
    if role.organization_id != org_id:
        raise HTTPException(status_code=404, detail="Role not found")

    in_use = (
        await db.execute(
            select(func.count()).select_from(UserRole).where(UserRole.role_id == role_id)
        )
    ).scalar() or 0
    if in_use:
        raise HTTPException(
            status_code=409,
            detail=f"Role is assigned to {in_use} user(s); detach it first",
        )

    await db.delete(role)
    await db.commit()
    return None


def _authorize_role_grant(caller: User, roles_to_grant: list[Role]) -> None:
    """Refuse a privilege-escalating role assignment (issue #158).

    A ``user.manage`` holder must not be able to grant a role that carries more
    authority than the caller themselves holds — otherwise a custom "User Admin"
    role scoped to *only* ``user.manage`` could grant itself (or anyone) the
    system ``admin`` role and take over the org.

    Two guards, both must pass:

    * The system ``admin`` role may only be granted by a caller who is themselves
      an admin — ``admin`` carries non-catalog superuser authority (org settings,
      role CRUD) that the permission subset check below can't see.
    * The union of the *catalog* permissions the granted roles would confer must
      be a subset of the caller's own effective permissions — you can never hand
      out a sensitive permission you don't hold.
    """
    caller_holds_admin = any(
        r.name == ROLE_ADMIN and r.organization_id is None for r in (caller.roles or ())
    )
    granting_admin = any(r.name == ROLE_ADMIN and r.organization_id is None for r in roles_to_grant)
    if granting_admin and not caller_holds_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only an admin may grant the admin role.",
        )

    granted_perms: set[str] = set()
    for role in roles_to_grant:
        granted_perms |= permissions_for_role(
            name=role.name,
            organization_id=role.organization_id,
            permissions=role.permissions,
        )
    caller_perms = getattr(caller, "effective_permissions", None)
    if caller_perms is None:
        caller_perms = effective_permissions(caller.roles)
    if not granted_perms <= set(caller_perms):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You cannot grant permissions you do not hold.",
        )


def _validate_admin_set_password(password: str) -> None:
    """Run an admin-set password through the same complexity policy as
    self-service change-password (issue #158) — a ``user.manage`` actor must not
    be able to reset an account to a trivial value and log in as it."""
    try:
        validate_password_complexity(password)
    except PasswordError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/users", response_model=CreateUserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: CreateUserRequest,
    db: AsyncSession = Depends(get_control_db),
    # user.manage defaults to admin-only (unchanged) — a custom role can be
    # granted user management without inheriting the rest of `admin`.
    user: User = Depends(require_permission(PERM_USER_MANAGE)),
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
        must_change_password=True,
    )
    db.add(new_user)
    await db.flush()

    # Assign roles. Restrict the lookup to system roles + this org's
    # custom roles so an admin from acme can't accidentally (or
    # otherwise) grant a role minted by techflow.
    if body.role_names:
        result = await db.execute(
            select(Role).where(
                Role.name.in_(body.role_names),
                (Role.organization_id.is_(None)) | (Role.organization_id == org_id),
            )
        )
        roles = result.scalars().all()
        _authorize_role_grant(user, list(roles))
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
    current_user: User = Depends(require_permission(PERM_USER_MANAGE)),
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
        _validate_admin_set_password(body.password)
        target.hashed_password = pwd_context.hash(body.password)

    roles_changed = False
    if body.role_names is not None:
        new_role_names = sorted(set(body.role_names))
        roles_changed = new_role_names != previous_role_names
        await db.execute(UserRole.__table__.delete().where(UserRole.user_id == user_id))
        if body.role_names:
            result = await db.execute(
                select(Role).where(
                    Role.name.in_(body.role_names),
                    (Role.organization_id.is_(None)) | (Role.organization_id == org_id),
                )
            )
            roles = result.scalars().all()
            _authorize_role_grant(current_user, list(roles))
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


class UserDeleteConflict(BaseModel):
    """Why a user can't be deleted — surfaced in the 409 body so the UI can list it."""

    open_invoice_assignments: int  # invoices.assigned_to_id, status not in (done, rejected, paid)
    pending_approval_steps: int  # workflow_steps.assigned_to (incomplete)
    active_workflow_approver_in: int  # workflow_definitions.steps_config approver_ids contains user


async def _user_reference_counts(db_name: str, user_id: uuid.UUID) -> UserDeleteConflict:
    """Count tenant-DB rows that reference this user and would be orphaned by delete.

    All three fields must be zero for a delete to proceed. Audit-log /
    payment-initiator / invoice-uploader references are intentionally
    excluded — those are historical and must survive the user.
    """
    engine = get_tenant_engine(db_name)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        # Open invoices currently assigned to this user.
        invoice_q = await session.execute(
            text(
                "SELECT COUNT(*) FROM invoices "
                "WHERE assigned_to_id = :uid "
                "AND status NOT IN ('done', 'rejected', 'paid')"
            ),
            {"uid": user_id},
        )
        open_invoices = int(invoice_q.scalar() or 0)

        # Workflow steps awaiting their approval.
        step_q = await session.execute(
            text(
                "SELECT COUNT(*) FROM workflow_steps "
                "WHERE (assigned_to = :uid OR original_assigned_to = :uid) "
                "AND completed_at IS NULL"
            ),
            {"uid": user_id},
        )
        pending_steps = int(step_q.scalar() or 0)

        # Active workflow definitions whose approver_ids list contains this user.
        # @> requires a JSONB containment check; the user_id can appear at the
        # step.config.approver_ids level OR inside step.config.approval_chain[].approver_ids.
        # JSON path '$ ?? @ == "uid"' isn't portable across pg versions; the
        # straightforward route is a text-match, which is good enough for a
        # blocklist (false positives only happen if a user's UUID literally
        # appears as a substring elsewhere — UUIDs are uncorrelated, so the
        # collision rate is effectively zero).
        defn_q = await session.execute(
            text(
                "SELECT COUNT(*) FROM workflow_definitions "
                "WHERE is_active = true "
                "AND steps_config::text LIKE :pattern"
            ),
            {"pattern": f"%{user_id}%"},
        )
        active_defs = int(defn_q.scalar() or 0)

        return UserDeleteConflict(
            open_invoice_assignments=open_invoices,
            pending_approval_steps=pending_steps,
            active_workflow_approver_in=active_defs,
        )


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_control_db),
    current_user: User = Depends(require_permission(PERM_USER_MANAGE)),
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

    # Pre-flight safety: refuse if the user is referenced by anything that
    # would be silently orphaned. The admin must reassign references first.
    org_q = await db.execute(select(Organization).where(Organization.id == org_id))
    org = org_q.scalar_one()
    conflict = await _user_reference_counts(org.db_name, user_id)
    if (
        conflict.open_invoice_assignments
        or conflict.pending_approval_steps
        or conflict.active_workflow_approver_in
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "message": (
                    "Cannot delete user — they are still referenced by "
                    "in-flight work. Reassign these first, then retry."
                ),
                "references": conflict.model_dump(),
            },
        )

    # Remove role assignments first
    await db.execute(UserRole.__table__.delete().where(UserRole.user_id == user_id))
    await db.delete(target)
    await db.commit()

    # Deletion → revoke any active JWT so the tombstoned user can't keep
    # hitting the API until their token expires.
    await revoke_user_sessions(user_id)


class BulkDeleteRequest(BaseModel):
    user_ids: list[str]


class BulkDeleteFailure(BaseModel):
    user_id: str
    reason: str  # "not_found" | "self" | "blocked"
    references: UserDeleteConflict | None = None


class BulkDeleteResponse(BaseModel):
    deleted: list[str]
    failed: list[BulkDeleteFailure]


@router.post("/users/bulk-delete", response_model=BulkDeleteResponse)
async def bulk_delete_users(
    body: BulkDeleteRequest,
    db: AsyncSession = Depends(get_control_db),
    current_user: User = Depends(require_permission(PERM_USER_MANAGE)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    """Best-effort delete of multiple users.

    Each id is processed independently; a single failure does not
    short-circuit the others. The response splits successes from failures
    so the UI can refresh the table and show a per-row reason for the
    ones that didn't go through.
    """
    org_q = await db.execute(select(Organization).where(Organization.id == org_id))
    org = org_q.scalar_one()

    deleted: list[str] = []
    failed: list[BulkDeleteFailure] = []

    for raw_id in body.user_ids:
        try:
            user_uuid = uuid.UUID(raw_id)
        except ValueError:
            failed.append(BulkDeleteFailure(user_id=raw_id, reason="not_found"))
            continue

        if user_uuid == current_user.id:
            failed.append(BulkDeleteFailure(user_id=raw_id, reason="self"))
            continue

        result = await db.execute(
            select(User).where(User.id == user_uuid, User.organization_id == org_id)
        )
        target = result.scalar_one_or_none()
        if not target:
            failed.append(BulkDeleteFailure(user_id=raw_id, reason="not_found"))
            continue

        conflict = await _user_reference_counts(org.db_name, user_uuid)
        if (
            conflict.open_invoice_assignments
            or conflict.pending_approval_steps
            or conflict.active_workflow_approver_in
        ):
            failed.append(BulkDeleteFailure(user_id=raw_id, reason="blocked", references=conflict))
            continue

        await db.execute(UserRole.__table__.delete().where(UserRole.user_id == user_uuid))
        await db.delete(target)
        deleted.append(raw_id)

    await db.commit()

    for raw_id in deleted:
        await revoke_user_sessions(uuid.UUID(raw_id))

    return BulkDeleteResponse(deleted=deleted, failed=failed)
