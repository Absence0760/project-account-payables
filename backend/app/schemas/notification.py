"""Pydantic schemas for the notification center + per-user preferences."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.api.pagination import PageMeta
from app.models.notification import NOTIFICATION_EVENT_TYPES


class NotificationResponse(BaseModel):
    id: uuid.UUID
    event_type: str
    entity_type: str
    entity_id: uuid.UUID | None = None
    title: str
    body: str | None = None
    read_at: datetime | None = None
    created_at: datetime


class NotificationListResponse(PageMeta):
    """Paginated list of the current user's notifications.

    `unread` is the total unread count for the user (independent of the page
    window), so the sidebar badge can use the same response.
    """

    items: list[NotificationResponse]
    total: int
    unread: int


class ChannelPrefs(BaseModel):
    """Per-event delivery channels."""

    email: bool = True
    in_app: bool = True


class NotificationPrefs(BaseModel):
    """The full preference map: one ChannelPrefs per notifiable event type.

    Missing keys fall back to defaults (all channels on) — see
    services/notification_dispatch.resolve_prefs.
    """

    invoice_assigned: ChannelPrefs = Field(default_factory=ChannelPrefs)
    invoice_approved: ChannelPrefs = Field(default_factory=ChannelPrefs)
    invoice_rejected: ChannelPrefs = Field(default_factory=ChannelPrefs)
    invoice_paid: ChannelPrefs = Field(default_factory=ChannelPrefs)


class NotificationPrefsUpdate(BaseModel):
    """Partial update — only the supplied event types are changed."""

    invoice_assigned: ChannelPrefs | None = None
    invoice_approved: ChannelPrefs | None = None
    invoice_rejected: ChannelPrefs | None = None
    invoice_paid: ChannelPrefs | None = None


class MarkReadResponse(BaseModel):
    id: uuid.UUID
    read_at: datetime | None = None


class ReadAllResponse(BaseModel):
    updated: int


class UnreadCountResponse(BaseModel):
    unread: int


# Re-export for callers that want the canonical event list.
__all__ = [
    "NotificationResponse",
    "NotificationListResponse",
    "ChannelPrefs",
    "NotificationPrefs",
    "NotificationPrefsUpdate",
    "MarkReadResponse",
    "ReadAllResponse",
    "UnreadCountResponse",
    "NOTIFICATION_EVENT_TYPES",
]
