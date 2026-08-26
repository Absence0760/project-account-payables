"""Real-DB coverage for the admin "Invite User" welcome email
(``POST /api/admin/users``).

Before this, admin-created users got a temp password shown once in the API
response for the admin to relay out-of-band — never an actual email. This
mirrors the existing supplier-portal invite email
(``app/api/vendors.py::invite_vendor_portal_user`` /
``tests/test_vendor_portal_invite.py``): best-effort send via the configured
email adapter, tenant URL built from ``FEOH_TENANT_URL_TEMPLATE``, and the
temp password kept on the API response for backward compat (local dev may
have no email configured).
"""

from __future__ import annotations

import uuid

import pytest


@pytest.mark.asyncio
async def test_create_user_sends_welcome_email(realdb, monkeypatch):
    from app.config import settings as cfg

    monkeypatch.setattr(cfg, "tenant_url_template", "https://{slug}.feohledger.test", raising=True)

    sent: list = []

    async def _fake_send(self, message):  # noqa: ANN001
        sent.append(message)

    from app.services.email_adapters.console_adapter import ConsoleAdapter

    monkeypatch.setattr(ConsoleAdapter, "send", _fake_send, raising=True)

    slug = realdb.info("a").slug
    email = f"new-hire-{uuid.uuid4().hex[:8]}@acme.test"

    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post(
            "/api/admin/users",
            json={"email": email, "full_name": "New Hire", "role_names": ["ap_clerk"]},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        new_id = body["id"]
        temp_password = body["temporary_password"]
        try:
            # Backward compat: the temp password is still on the response.
            assert temp_password
            assert len(sent) == 1
            msg = sent[0]
            assert msg.to == email
            assert f"https://{slug}.feohledger.test" in msg.body_text
            assert temp_password in msg.body_text
            assert email in msg.body_text
        finally:
            await c.delete(f"/api/admin/users/{new_id}")


@pytest.mark.asyncio
async def test_create_user_email_failure_does_not_break_creation(realdb, monkeypatch):
    """A failed send is best-effort — user creation must still succeed and
    still return the temp password, same posture as every other notification
    in this codebase."""

    async def _fake_send(self, message):  # noqa: ANN001
        raise RuntimeError("smtp unreachable")

    from app.services.email_adapters.console_adapter import ConsoleAdapter

    monkeypatch.setattr(ConsoleAdapter, "send", _fake_send, raising=True)

    email = f"new-hire-{uuid.uuid4().hex[:8]}@acme.test"

    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post(
            "/api/admin/users",
            json={"email": email, "full_name": "New Hire", "role_names": []},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["temporary_password"]
        new_id = body["id"]
        await c.delete(f"/api/admin/users/{new_id}")
