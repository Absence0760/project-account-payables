"""`notify_event` reports how many recipients it actually actioned.

The dispatcher is best-effort and never raises, which is right for the ~35
`transition_invoice` call sites: "nobody had this event turned on" is a normal
outcome for a status change. But two callers write a **suppress-forever marker**
right after it — `cash_flow_alerts` stamps the alerted-period marker, and
`contract_renewal` stamps `renewal_alert_sent_at`, which only
`POST /api/contracts/{id}/renew` ever clears. Both already skipped the marker
when they resolved zero recipients; that covers exactly one of the ways this can
reach nobody.

`notify_event` returned `None` on every other silent exit — the master
`FEOH_NOTIFICATIONS_ENABLED` switch off, an unknown event type, a template
render that raised, the recipient load failing, every resolved recipient
inactive or opted out — so those callers stamped their marker and the finance
leaders were never told about that projected shortfall period, or that
contract's renewal, for the rest of its life. Nothing counted as a failure
either, so `GET /api/health/sweeps` stayed green.

These pin the count so that can't silently return.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import settings
from app.models.notification import EVENT_INVOICE_APPROVED
from app.services import notification_dispatch as nd
from app.services.notification_templates import RenderedNotification


def _rendered() -> RenderedNotification:
    return RenderedNotification(title="t", body_text="b", body_html=None)


def _user(uid, *, email="a@b.test", active=True, prefs=None):
    return SimpleNamespace(
        id=uid, email=email, is_active=active, notification_prefs=prefs, locale=None
    )


def _session() -> MagicMock:
    """A stand-in tenant session: `notify_event` only calls `.add()` on it and
    hands a factory to `enqueue_post_commit`, which reads `.info`."""
    s = MagicMock()
    s.info = {}
    return s


async def _notify(db, recipients, **kw):
    return await nd.notify_event(
        db,
        correlation_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        event_type=kw.pop("event_type", EVENT_INVOICE_APPROVED),
        entity_id=uuid.uuid4(),
        recipient_user_ids=recipients,
        rendered=_rendered(),
        entity_type=kw.pop("entity_type", "contract"),
        **kw,
    )


@pytest.mark.asyncio
async def test_counts_every_recipient_actioned():
    uid_a, uid_b = uuid.uuid4(), uuid.uuid4()
    users = {uid_a: _user(uid_a), uid_b: _user(uid_b)}
    with patch.object(nd, "_load_recipients", AsyncMock(return_value=users)):
        n = await _notify(_session(), [uid_a, uid_b])
    assert n == 2


@pytest.mark.asyncio
async def test_master_switch_off_reports_zero(monkeypatch):
    monkeypatch.setattr(settings, "notifications_enabled", False)
    uid = uuid.uuid4()
    with patch.object(nd, "_load_recipients", AsyncMock(return_value={uid: _user(uid)})):
        n = await _notify(_session(), [uid])
    assert n == 0


@pytest.mark.asyncio
async def test_unknown_event_type_reports_zero():
    uid = uuid.uuid4()
    with patch.object(nd, "_load_recipients", AsyncMock(return_value={uid: _user(uid)})):
        n = await _notify(_session(), [uid], event_type="not_a_real_event")
    assert n == 0


@pytest.mark.asyncio
async def test_recipient_load_failure_reports_zero():
    """A control-plane blip must not read as a delivered alert."""
    with patch.object(nd, "_load_recipients", AsyncMock(side_effect=RuntimeError("db down"))):
        n = await _notify(_session(), [uuid.uuid4()])
    assert n == 0


@pytest.mark.asyncio
async def test_every_recipient_opted_out_reports_zero():
    """Recipients exist and resolve, but all channels are off for this event."""
    uid = uuid.uuid4()
    off = {EVENT_INVOICE_APPROVED: {"email": False, "in_app": False}}
    with patch.object(nd, "_load_recipients", AsyncMock(return_value={uid: _user(uid, prefs=off)})):
        n = await _notify(_session(), [uid])
    assert n == 0


@pytest.mark.asyncio
async def test_inactive_recipient_is_not_counted():
    uid = uuid.uuid4()
    with patch.object(
        nd, "_load_recipients", AsyncMock(return_value={uid: _user(uid, active=False)})
    ):
        n = await _notify(_session(), [uid])
    assert n == 0


@pytest.mark.asyncio
async def test_in_app_only_recipient_still_counts():
    """The in-app row IS the notification for a user with email off — the early
    return that skips the outbound queue must not report zero."""
    uid = uuid.uuid4()
    prefs = {EVENT_INVOICE_APPROVED: {"email": False, "in_app": True}}
    db = _session()
    with patch.object(
        nd, "_load_recipients", AsyncMock(return_value={uid: _user(uid, prefs=prefs)})
    ):
        n = await _notify(db, [uid])
    assert n == 1
    db.add.assert_called_once()
