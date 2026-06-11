"""Notification center + per-user preference endpoints.

In-app notifications live in the tenant DB (`get_tenant_db`), always scoped to
`recipient_user_id == current_user.id` so one user can never read another's.
Preferences are user-global and live on the control-plane `User` row
(`get_control_db`).

Every endpoint is behind an auth dependency. Notifications are per-user, not
role-gated, so the gate accepts ALL roles via `require_roles(*ALL_ROLES)` —
which still satisfies the "no endpoint without an auth dependency" coverage gate
in `tests/test_rbac.py`.
"""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ALL_ROLES, require_roles
from app.api.pagination import PaginationParams, pagination_params
from app.database import get_control_db
from app.models.notification import Notification
from app.models.user import User
from app.schemas.notification import (
    MarkReadResponse,
    NotificationListResponse,
    NotificationPrefs,
    NotificationPrefsUpdate,
    NotificationResponse,
    ReadAllResponse,
    UnreadCountResponse,
)
from app.tenant import get_tenant_db

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _to_response(n: Notification) -> NotificationResponse:
    return NotificationResponse(
        id=n.id,
        event_type=n.event_type,
        entity_type=n.entity_type,
        entity_id=n.entity_id,
        title=n.title,
        body=n.body,
        read_at=n.read_at,
        created_at=n.created_at,
    )


async def _unread_count(db: AsyncSession, user_id: uuid.UUID) -> int:
    result = await db.execute(
        select(func.count())
        .select_from(Notification)
        .where(
            Notification.recipient_user_id == user_id,
            Notification.read_at.is_(None),
        )
    )
    return int(result.scalar() or 0)


@router.get("", response_model=NotificationListResponse)
async def list_notifications(
    unread_only: bool = Query(False),
    pagination: PaginationParams = Depends(pagination_params),
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(*ALL_ROLES)),
):
    """List the current user's notifications, newest first.

    `unread_only=true` filters to unread. `unread` in the envelope is the
    user's *total* unread count regardless of the filter / page window.
    """
    base = select(Notification).where(Notification.recipient_user_id == user.id)
    if unread_only:
        base = base.where(Notification.read_at.is_(None))

    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar() or 0

    rows = (
        (
            await db.execute(
                base.order_by(Notification.created_at.desc())
                .offset(pagination.offset)
                .limit(pagination.limit)
            )
        )
        .scalars()
        .all()
    )

    return NotificationListResponse(
        items=[_to_response(n) for n in rows],
        total=int(total),
        unread=await _unread_count(db, user.id),
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.get("/unread-count", response_model=UnreadCountResponse)
async def unread_count(
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(*ALL_ROLES)),
):
    """Cheap unread count for the sidebar badge."""
    return UnreadCountResponse(unread=await _unread_count(db, user.id))


# ---------- Preferences (control plane) -----------------------------------
# Registered BEFORE the parameterised `/{notification_id}/read` so the literal
# `/preferences` path isn't matched as notification_id="preferences".


@router.get("/preferences", response_model=NotificationPrefs)
async def get_preferences(
    user: User = Depends(require_roles(*ALL_ROLES)),
):
    """Return the current user's notification preferences (defaults if unset)."""
    return NotificationPrefs(**(user.notification_prefs or {}))


@router.patch("/preferences", response_model=NotificationPrefs)
async def update_preferences(
    body: NotificationPrefsUpdate,
    ctrl_db: AsyncSession = Depends(get_control_db),
    user: User = Depends(require_roles(*ALL_ROLES)),
):
    """Partially update notification preferences. Only supplied event types
    are changed; unspecified ones keep their current value (or default)."""
    current = NotificationPrefs(**(user.notification_prefs or {}))
    merged = current.model_dump()
    for event_type, channels in body.model_dump(exclude_none=True).items():
        merged[event_type] = channels

    # Re-fetch the row on the control session so the write is bound to it.
    target = (await ctrl_db.execute(select(User).where(User.id == user.id))).scalar_one()
    target.notification_prefs = merged
    await ctrl_db.commit()

    return NotificationPrefs(**merged)


@router.post("/read-all", response_model=ReadAllResponse)
async def mark_all_read(
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(*ALL_ROLES)),
):
    """Mark every unread notification for the current user as read."""
    now = datetime.now(UTC)
    result = await db.execute(
        update(Notification)
        .where(
            Notification.recipient_user_id == user.id,
            Notification.read_at.is_(None),
        )
        .values(read_at=now)
    )
    await db.commit()
    return ReadAllResponse(updated=int(result.rowcount or 0))


@router.post("/{notification_id}/read", response_model=MarkReadResponse)
async def mark_read(
    notification_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(*ALL_ROLES)),
):
    """Mark one notification read. 404 for missing OR another user's row —
    same response either way so it can't enumerate other users' ids."""
    result = await db.execute(
        select(Notification).where(
            Notification.id == notification_id,
            Notification.recipient_user_id == user.id,
        )
    )
    n = result.scalar_one_or_none()
    if n is None:
        raise HTTPException(status_code=404, detail="Notification not found")

    # Naturally idempotent — re-marking a read row is a no-op.
    if n.read_at is None:
        n.read_at = datetime.now(UTC)
        await db.commit()

    return MarkReadResponse(id=n.id, read_at=n.read_at)
