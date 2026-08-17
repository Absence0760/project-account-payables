"""Chat-notification admin surface (`/api/organization/chat-notifications`).

`Organization.settings.chat_notifications.webhook_url` is the credential for
both real chat providers — a Slack / Teams incoming-webhook URL lets whoever
holds it post arbitrary content into the customer's approval channel, with no
authentication. Before this surface existed it was only settable by overwriting
the settings JSON through the generic `PATCH /api/organization`, so recovering
from a leak was an untracked hand-edit.

Covers:

  * RBAC — admin-only on every verb, including the READ (the response carries
    the webhook's hostname).
  * The URL is **write-only**: it never appears in any response body, and a
    refusal never echoes it into an error body either.
  * `PUT ""` (config) **preserves** the credential — the regression the split
    between the two endpoints exists to prevent.
  * `PUT /webhook` replaces atomically (no overlap slot — a destination has no
    counterpart to overlap) and `DELETE /webhook` revokes, idempotently.
  * Both webhook verbs write a PII-free `organization.chat_webhook_rotated`
    audit row carrying hostnames only — never the URL.
  * The write path applies the SAME SSRF rule the adapters apply at send time,
    so "saved" implies "the sender won't silently skip it".

Isolation note: `settings.chat_notifications` lives on the shared control-plane
Organization row, so every mutating test resets it in a `finally`.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.organization import Organization
from app.models.workflow import AuditLog

# `.invalid` is reserved by RFC 6761 and never resolves, so `assert_public_url`
# takes its documented "unresolvable → leave alone" branch. That keeps these
# tests off the network without weakening the guard being exercised.
SLACK_URL = "https://hooks.chat-test.invalid/services/T0AAAAAAA/B0BBBBBBB/zzTOPSECRETzz"
SLACK_URL_2 = "https://hooks2.chat-test.invalid/services/T0AAAAAAA/B0CCCCCCC/qqROTATEDqq"
TOKEN_FRAGMENT = "zzTOPSECRETzz"


async def _reset(realdb, key: str = "a") -> None:
    """Clear the tenant's chat block so a mutating test can't leak state."""
    async with realdb.client(key=key, role="admin") as c:
        await c.delete("/api/organization/chat-notifications/webhook")
        await c.put(
            "/api/organization/chat-notifications",
            json={"enabled": False, "provider": "mock", "events": {}},
        )


async def _stored_config(realdb, key: str = "a") -> dict:
    cmk = realdb.control_sessionmaker()
    async with cmk() as s:
        org = (
            await s.execute(select(Organization).where(Organization.id == realdb.info(key).org_id))
        ).scalar_one()
    return (org.settings or {}).get("chat_notifications") or {}


async def _audit_rows(realdb, action: str, key: str = "a") -> list[AuditLog]:
    tmk = realdb.sessionmaker(key)
    async with tmk() as s:
        return list(
            (await s.execute(select(AuditLog).where(AuditLog.action == action))).scalars().all()
        )


# ---------- RBAC -------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_requires_auth(realdb):
    async with realdb.client(key="a", role=None) as c:
        resp = await c.get("/api/organization/chat-notifications")
    assert resp.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["ap_clerk", "ap_manager", "cfo"])
async def test_get_is_admin_only(realdb, role):
    """Admin-only on the READ too: the response carries the webhook hostname,
    which is closer to the credential than any non-admin role needs."""
    async with realdb.client(key="a", role=role) as c:
        resp = await c.get("/api/organization/chat-notifications")
    assert resp.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["ap_clerk", "ap_manager", "cfo"])
async def test_webhook_writes_are_admin_only(realdb, role):
    async with realdb.client(key="a", role=role) as c:
        put_config = await c.put(
            "/api/organization/chat-notifications",
            json={"enabled": True, "provider": "mock", "events": {}},
        )
        put_hook = await c.put(
            "/api/organization/chat-notifications/webhook",
            json={"webhook_url": SLACK_URL},
        )
        delete_hook = await c.delete("/api/organization/chat-notifications/webhook")
    assert put_config.status_code == 403
    assert put_hook.status_code == 403
    assert delete_hook.status_code == 403


# ---------- default read -----------------------------------------------------


@pytest.mark.asyncio
async def test_get_defaults_for_an_unconfigured_org(realdb):
    await _reset(realdb)
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.get("/api/organization/chat-notifications")
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is False
    assert body["webhook_configured"] is False
    assert body["webhook_host"] is None
    # Registry-derived, so the picker can't offer a provider with no adapter.
    assert "mock" in body["supported_providers"]
    assert "slack" in body["supported_providers"]
    assert "invoice_approved" in body["supported_events"]
    assert "webhook_url" not in body


# ---------- the URL is write-only -------------------------------------------


@pytest.mark.asyncio
async def test_webhook_url_never_appears_in_any_response(realdb):
    """The single most important property: set it, and no endpoint hands it
    back — only whether one is set plus the bare hostname."""
    try:
        async with realdb.client(key="a", role="admin") as c:
            put = await c.put(
                "/api/organization/chat-notifications/webhook",
                json={"webhook_url": SLACK_URL},
            )
            assert put.status_code == 200
            get = await c.get("/api/organization/chat-notifications")
            org = await c.get("/api/organization")

        for resp in (put, get):
            body = resp.json()
            assert body["webhook_configured"] is True
            assert body["webhook_host"] == "hooks.chat-test.invalid"
            assert TOKEN_FRAGMENT not in resp.text
            assert "/services/" not in resp.text

        # It IS persisted — the endpoint stored the credential, it just doesn't
        # serve it. (The generic `GET /api/organization` still returns the raw
        # settings JSONB, which is a separate, pre-existing exposure tracked
        # outside this round — assert only that the dedicated surface is clean.)
        assert (await _stored_config(realdb))["webhook_url"] == SLACK_URL
        assert org.status_code == 200
    finally:
        await _reset(realdb)


@pytest.mark.asyncio
async def test_refusal_never_echoes_the_credential(realdb):
    """FastAPI's default validation body echoes the offending `input`, which is
    why the URL field carries no Pydantic constraints and every check answers
    with one generic, value-free 422."""
    bad = f"ftp://hooks.chat-test.invalid/services/{TOKEN_FRAGMENT}"
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.put(
            "/api/organization/chat-notifications/webhook",
            json={"webhook_url": bad},
        )
    assert resp.status_code == 422
    assert TOKEN_FRAGMENT not in resp.text
    assert "hooks.chat-test.invalid" not in resp.text


# ---------- rotation ---------------------------------------------------------


@pytest.mark.asyncio
async def test_rotate_replaces_atomically_and_audits_hostnames_only(realdb):
    try:
        async with realdb.client(key="a", role="admin") as c:
            await c.put(
                "/api/organization/chat-notifications",
                json={"enabled": True, "provider": "slack", "events": {}},
            )
            await c.put(
                "/api/organization/chat-notifications/webhook",
                json={"webhook_url": SLACK_URL},
            )
            resp = await c.put(
                "/api/organization/chat-notifications/webhook",
                json={"webhook_url": SLACK_URL_2},
            )
        assert resp.status_code == 200
        assert resp.json()["webhook_host"] == "hooks2.chat-test.invalid"

        stored = await _stored_config(realdb)
        # Exactly ONE url key survives — no `previous_webhook_url` overlap slot,
        # because a destination has no counterparty holding the old value and an
        # overlap would keep posting into the compromised channel.
        assert stored["webhook_url"] == SLACK_URL_2
        assert [k for k in stored if "webhook" in k] == ["webhook_url"]

        rows = await _audit_rows(realdb, "organization.chat_webhook_rotated")
        assert len(rows) == 2
        latest = rows[-1].details
        assert latest["removed"] is False
        assert latest["previous_configured"] is True
        assert latest["previous_host"] == "hooks.chat-test.invalid"
        assert latest["new_host"] == "hooks2.chat-test.invalid"
        assert latest["provider"] == "slack"
        # The credential itself never enters the WORM-shipped trail.
        assert TOKEN_FRAGMENT not in str(latest)
        assert "/services/" not in str(latest)
    finally:
        await _reset(realdb)


@pytest.mark.asyncio
async def test_delete_revokes_and_is_idempotent(realdb):
    try:
        async with realdb.client(key="a", role="admin") as c:
            await c.put(
                "/api/organization/chat-notifications",
                json={"enabled": True, "provider": "slack", "events": {}},
            )
            await c.put(
                "/api/organization/chat-notifications/webhook",
                json={"webhook_url": SLACK_URL},
            )
            first = await c.delete("/api/organization/chat-notifications/webhook")
            second = await c.delete("/api/organization/chat-notifications/webhook")

        assert first.status_code == second.status_code == 200
        assert first.json()["webhook_configured"] is False
        assert second.json()["webhook_configured"] is False

        stored = await _stored_config(realdb)
        assert "webhook_url" not in stored
        # Revoking the credential must not disturb the rest of the config —
        # containment shouldn't silently rewrite which provider is selected.
        assert stored["enabled"] is True
        assert stored["provider"] == "slack"

        rows = await _audit_rows(realdb, "organization.chat_webhook_rotated")
        removals = [r for r in rows if r.details.get("removed") is True]
        assert len(removals) == 2  # idempotent in effect, still audited each time
        assert removals[0].details["previous_host"] == "hooks.chat-test.invalid"
        assert removals[0].details["new_host"] is None
        assert removals[1].details["previous_configured"] is False
    finally:
        await _reset(realdb)


@pytest.mark.asyncio
async def test_empty_url_is_refused_rather_than_silently_revoking(realdb):
    """Removal has its own verb, so a form field that failed to populate can't
    quietly disable the approval channel."""
    try:
        async with realdb.client(key="a", role="admin") as c:
            await c.put(
                "/api/organization/chat-notifications/webhook",
                json={"webhook_url": SLACK_URL},
            )
            resp = await c.put(
                "/api/organization/chat-notifications/webhook",
                json={"webhook_url": "   "},
            )
        assert resp.status_code == 422
        assert (await _stored_config(realdb))["webhook_url"] == SLACK_URL
    finally:
        await _reset(realdb)


# ---------- the SSRF gate matches the sender's ------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/services/hook",
        "http://169.254.169.254/latest/meta-data",
        "http://10.0.0.5/hook",
        "http://[::1]/hook",
    ],
)
async def test_non_public_targets_are_refused(realdb, url):
    """The same `is_public_url` rule the Slack/Teams adapters apply at send
    time — so a URL that saves is a URL that will actually post."""
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.put(
            "/api/organization/chat-notifications/webhook",
            json={"webhook_url": url},
        )
    assert resp.status_code == 422
    assert (await _stored_config(realdb)).get("webhook_url") is None


@pytest.mark.asyncio
async def test_over_length_url_is_refused(realdb):
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.put(
            "/api/organization/chat-notifications/webhook",
            json={"webhook_url": "https://hooks.chat-test.invalid/" + ("x" * 2100)},
        )
    assert resp.status_code == 422


# ---------- config save preserves the credential ----------------------------


@pytest.mark.asyncio
async def test_config_put_preserves_the_webhook_url(realdb):
    """The regression the two-endpoint split exists to prevent: a whole-block
    replace here would silently drop the credential (the bug the branding
    endpoint once hit with `custom_domains`)."""
    try:
        async with realdb.client(key="a", role="admin") as c:
            await c.put(
                "/api/organization/chat-notifications/webhook",
                json={"webhook_url": SLACK_URL},
            )
            resp = await c.put(
                "/api/organization/chat-notifications",
                json={
                    "enabled": True,
                    "provider": "slack",
                    "events": {"invoice_paid": False},
                },
            )
        assert resp.status_code == 200
        assert resp.json()["webhook_configured"] is True
        assert resp.json()["enabled"] is True
        assert (await _stored_config(realdb))["webhook_url"] == SLACK_URL
    finally:
        await _reset(realdb)


@pytest.mark.asyncio
async def test_config_put_audits_pii_free(realdb):
    try:
        async with realdb.client(key="a", role="admin") as c:
            resp = await c.put(
                "/api/organization/chat-notifications",
                json={"enabled": True, "provider": "teams", "events": {"invoice_paid": False}},
            )
        assert resp.status_code == 200
        rows = await _audit_rows(realdb, "organization.chat_notifications_updated")
        assert rows
        details = rows[-1].details
        assert details["enabled"]["new"] is True
        assert details["provider"]["new"] == "teams"
        assert details["events"] == {"invoice_paid": False}
        assert "webhook" not in str(details)
    finally:
        await _reset(realdb)


@pytest.mark.asyncio
async def test_config_put_refuses_unknown_provider(realdb):
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.put(
            "/api/organization/chat-notifications",
            json={"enabled": True, "provider": "sl4ck", "events": {}},
        )
    assert resp.status_code == 422
    assert "sl4ck" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_config_put_refuses_unknown_event_key(realdb):
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.put(
            "/api/organization/chat-notifications",
            json={"enabled": True, "provider": "mock", "events": {"invoice_payed": True}},
        )
    assert resp.status_code == 422
    assert "invoice_payed" in resp.json()["detail"]


# ---------- tenant isolation -------------------------------------------------


@pytest.mark.asyncio
async def test_chat_config_is_tenant_scoped(realdb):
    """Tenant A's credential is invisible to tenant B's admin."""
    try:
        async with realdb.client(key="a", role="admin") as c:
            await c.put(
                "/api/organization/chat-notifications/webhook",
                json={"webhook_url": SLACK_URL},
            )
        async with realdb.client(key="b", role="admin") as c:
            resp = await c.get("/api/organization/chat-notifications")
        assert resp.status_code == 200
        assert resp.json()["webhook_configured"] is False
        assert resp.json()["webhook_host"] is None
    finally:
        await _reset(realdb, "a")
        await _reset(realdb, "b")
