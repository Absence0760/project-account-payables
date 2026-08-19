"""Guard: every notifiable event type is mutable through the preferences API.

`models/notification.NOTIFICATION_EVENT_TYPES` is the roster of events
`notification_dispatch.notify_event` will write and email. `resolve_prefs`
defaults a MISSING key to **on** (opt-out, not opt-in — a new user should get
notifications), so an event the prefs schema doesn't enumerate is not merely
undocumented: it is one the user cannot turn off at all.

That is exactly what happened. `schemas/notification.py` enumerated only the
four `invoice_*` events while the model declared seven, so every supplier-chat
message emailed the AP team with no opt-out, and the contract-renewal and
projected-cash-shortfall alerts were equally unmutable.

The population is DERIVED from the model's roster, so adding an event type fails
this file until it also joins both prefs schemas — there is no second list to
remember. Mirrors `tests/test_exception_type_labels.py`'s posture.

Pure-Python, no DB.
"""

from __future__ import annotations

from app.models.notification import NOTIFICATION_EVENT_TYPES
from app.schemas.notification import (
    ChannelPrefs,
    NotificationPrefs,
    NotificationPrefsUpdate,
)
from app.services.notification_dispatch import resolve_prefs


def test_read_schema_covers_exactly_the_roster():
    assert set(NotificationPrefs.model_fields) == set(NOTIFICATION_EVENT_TYPES), (
        "NotificationPrefs must enumerate every NOTIFICATION_EVENT_TYPES entry — "
        "a missing key defaults to ON in resolve_prefs, so the user can't mute it."
    )


def test_update_schema_covers_exactly_the_roster():
    assert set(NotificationPrefsUpdate.model_fields) == set(NOTIFICATION_EVENT_TYPES), (
        "NotificationPrefsUpdate must enumerate every NOTIFICATION_EVENT_TYPES "
        "entry — an event absent here is one a user can read but never change."
    )


def test_every_event_defaults_to_on():
    """The default is opt-OUT: a brand-new user gets everything. Pinning it here
    so the roster additions above can't accidentally arrive muted."""
    prefs = NotificationPrefs()
    for event in NOTIFICATION_EVENT_TYPES:
        channels = getattr(prefs, event)
        assert channels == ChannelPrefs(email=True, in_app=True)


def test_every_event_can_actually_be_muted_end_to_end():
    """The behaviour the schema gap broke: a stored `off` must survive
    `resolve_prefs`, for EVERY event — not just the four invoice ones."""
    stored = NotificationPrefs(
        **{event: ChannelPrefs(email=False, in_app=False) for event in NOTIFICATION_EVENT_TYPES}
    ).model_dump()

    for event in NOTIFICATION_EVENT_TYPES:
        assert resolve_prefs(stored, event) == {"email": False, "in_app": False}, (
            f"{event} cannot be muted"
        )


def test_a_partial_update_round_trips_a_newly_covered_event():
    """`PATCH /api/notifications/preferences` merges the supplied event types
    onto the stored map. The three events added to the roster must survive that
    merge, which is the path the API actually uses."""
    current = NotificationPrefs()
    body = NotificationPrefsUpdate(chat_message=ChannelPrefs(email=False, in_app=True))

    merged = current.model_dump()
    for field, value in body.model_dump(exclude_unset=True).items():
        merged[field] = value

    assert resolve_prefs(merged, "chat_message") == {"email": False, "in_app": True}
    # Untouched events keep their defaults.
    assert resolve_prefs(merged, "invoice_approved") == {"email": True, "in_app": True}
    assert resolve_prefs(merged, "cash_shortfall_projected") == {"email": True, "in_app": True}
