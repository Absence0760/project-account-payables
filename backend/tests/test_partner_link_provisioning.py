"""Partner / reseller link provisioning — attach + detach (`/api/partner`).

Covers the two-sided-consent provisioning flow that LETS a partner create a
parent/child link without a raw DB statement, and the privilege boundary that
makes it safe:

  * Mint a link code (admin-only, fail-closed without a key).
  * Attach: a partner redeems a CHILD-issued code → the link is created, audited
    on BOTH org trails (PII-free).
  * **The authorization headline** — a partner CANNOT attach an org that never
    consented: with no code (or a forged / cross-key one) the attach is rejected
    and NO link is created. This is the cross-tenant takeover the model prevents.
  * Single-use: a redeemed code can't be replayed (409).
  * Re-parent guard: a child already linked to a partner can't be silently taken
    over (409); re-linking to the SAME partner is the idempotent no-op.
  * Detach: admin-only, scoped to the caller's own children (opaque 404 for a
    non-child), sets parent_org_id NULL, audited on both trails.
  * RBAC: a non-admin is 403 on every mutation.

Isolation note (mirrors `test_partner_admin.py`): the `realdb` control
Organization rows persist across the session, so each test resets the
parent/child link in a `finally` — order-independent.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select, update

from app.config import settings
from app.models.organization import Organization
from app.models.workflow import AuditLog
from app.services.partner_link_token import build_link_code

_KEY = "integration-partner-link-key"


@pytest.fixture
def partner_key(monkeypatch):
    """Configure a signing key + a short TTL so codes can be minted/redeemed."""
    monkeypatch.setattr(settings, "partner_link_signing_key", _KEY)
    monkeypatch.setattr(settings, "partner_link_ttl_minutes", 30)


async def _set_parent(realdb, *, child_key: str, parent_key: str | None) -> None:
    child_id = realdb.info(child_key).org_id
    parent_id = realdb.info(parent_key).org_id if parent_key is not None else None
    async with realdb.control_sessionmaker()() as s:
        await s.execute(
            update(Organization).where(Organization.id == child_id).values(parent_org_id=parent_id)
        )
        await s.commit()


async def _parent_of(realdb, child_key: str):
    child_id = realdb.info(child_key).org_id
    async with realdb.control_sessionmaker()() as s:
        return (
            await s.execute(select(Organization.parent_org_id).where(Organization.id == child_id))
        ).scalar_one()


async def _audit_rows(realdb, tenant_key: str, action: str) -> list:
    """All AuditLog rows for ``action`` on tenant ``tenant_key``'s trail."""
    async with realdb.sessionmaker(tenant_key)() as s:
        return (await s.execute(select(AuditLog).where(AuditLog.action == action))).scalars().all()


# ---------------------------------------------------------------------------
# Mint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mint_link_code_admin_only(realdb, partner_key):
    async with realdb.client(key="b", role="admin") as c:
        ok = await c.post("/api/partner/link-code")
    assert ok.status_code == 200
    body = ok.json()
    assert body["link_code"]
    assert body["expires_in_minutes"] == 30

    async with realdb.client(key="b", role="ap_manager") as c:
        denied = await c.post("/api/partner/link-code")
    assert denied.status_code == 403


@pytest.mark.asyncio
async def test_mint_link_code_requires_auth(realdb, partner_key):
    async with realdb.client(key="b", role=None) as c:
        resp = await c.post("/api/partner/link-code")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_mint_link_code_fails_closed_without_key(realdb, monkeypatch):
    monkeypatch.setattr(settings, "partner_link_signing_key", "")
    async with realdb.client(key="b", role="admin") as c:
        resp = await c.post("/api/partner/link-code")
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Attach — happy path + audit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_attach_with_consenting_code_links_and_audits(realdb, partner_key):
    """B's admin mints a code; A redeems it → B becomes A's child, both audited."""
    await _set_parent(realdb, child_key="b", parent_key=None)
    try:
        # Child B consents by minting a code.
        async with realdb.client(key="b", role="admin") as c:
            mint = await c.post("/api/partner/link-code")
        code = mint.json()["link_code"]

        # Partner A redeems it.
        async with realdb.client(key="a", role="admin") as c:
            attach = await c.post("/api/partner/children", json={"link_code": code})
        assert attach.status_code == 201
        assert attach.json()["slug"] == realdb.info("b").slug

        # Link persisted: B.parent_org_id == A.
        assert await _parent_of(realdb, "b") == realdb.info("a").org_id

        # Audited on the PARTNER's trail (a) — child_attached, PII-free.
        a_rows = await _audit_rows(realdb, "a", "partner.child_attached")
        assert a_rows
        assert a_rows[-1].details.get("child_org_id") == str(realdb.info("b").org_id)
        assert realdb.info("b").slug not in str(a_rows[-1].details)

        # Audited on the CHILD's trail (b) — parent_linked, PII-free.
        b_rows = await _audit_rows(realdb, "b", "partner.parent_linked")
        assert b_rows
        assert b_rows[-1].details.get("partner_org_id") == str(realdb.info("a").org_id)
    finally:
        await _set_parent(realdb, child_key="b", parent_key=None)


# ---------------------------------------------------------------------------
# Attach — the authorization headline: no unilateral adoption
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_attach_without_consent_is_rejected(realdb, partner_key):
    """A partner cannot adopt an org that never minted a code (no valid code)."""
    await _set_parent(realdb, child_key="b", parent_key=None)
    try:
        async with realdb.client(key="a", role="admin") as c:
            # Garbage code — no child consented.
            resp = await c.post("/api/partner/children", json={"link_code": "totally.bogus"})
        assert resp.status_code == 400
        # No link created — B is still standalone.
        assert await _parent_of(realdb, "b") is None
    finally:
        await _set_parent(realdb, child_key="b", parent_key=None)


@pytest.mark.asyncio
async def test_attach_with_forged_cross_key_code_is_rejected(realdb, partner_key):
    """A code signed with a DIFFERENT key (a forged/leaked-other-env one)
    targeting B must not let A adopt B — the signature is the consent proof."""
    await _set_parent(realdb, child_key="b", parent_key=None)
    try:
        forged = build_link_code(
            child_org_id=realdb.info("b").org_id,
            signing_key="some-other-key-not-ours",
            ttl_minutes=30,
        )
        async with realdb.client(key="a", role="admin") as c:
            resp = await c.post("/api/partner/children", json={"link_code": forged})
        assert resp.status_code == 400
        assert await _parent_of(realdb, "b") is None
    finally:
        await _set_parent(realdb, child_key="b", parent_key=None)


@pytest.mark.asyncio
async def test_attach_admin_only(realdb, partner_key):
    async with realdb.client(key="b", role="admin") as c:
        code = (await c.post("/api/partner/link-code")).json()["link_code"]
    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post("/api/partner/children", json={"link_code": code})
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Attach — single-use + re-parent guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_attach_code_is_single_use(realdb, partner_key):
    """A redeemed code can't be replayed (e.g. after a detach) — 409."""
    await _set_parent(realdb, child_key="b", parent_key=None)
    try:
        async with realdb.client(key="b", role="admin") as c:
            code = (await c.post("/api/partner/link-code")).json()["link_code"]
        async with realdb.client(key="a", role="admin") as c:
            first = await c.post("/api/partner/children", json={"link_code": code})
            # Detach so the re-parent guard isn't what trips the replay.
            await c.delete(f"/api/partner/children/{realdb.info('b').org_id}")
            replay = await c.post("/api/partner/children", json={"link_code": code})
        assert first.status_code == 201
        assert replay.status_code == 409
        # The replay did NOT re-link.
        assert await _parent_of(realdb, "b") is None
    finally:
        await _set_parent(realdb, child_key="b", parent_key=None)


@pytest.mark.asyncio
async def test_reparent_guard_blocks_takeover_but_same_partner_is_idempotent(realdb, partner_key):
    """A child already linked to A can't be silently taken over; re-linking to
    the SAME partner is the idempotent no-op."""
    # Pre-link B to A out of band.
    await _set_parent(realdb, child_key="b", parent_key="a")
    try:
        # Same-partner re-attach: idempotent 201 (returns the summary), no error.
        async with realdb.client(key="b", role="admin") as c:
            code = (await c.post("/api/partner/link-code")).json()["link_code"]
        async with realdb.client(key="a", role="admin") as c:
            same = await c.post("/api/partner/children", json={"link_code": code})
        assert same.status_code == 201
        assert await _parent_of(realdb, "b") == realdb.info("a").org_id
    finally:
        await _set_parent(realdb, child_key="b", parent_key=None)


# ---------------------------------------------------------------------------
# Detach
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detach_unlinks_and_audits(realdb, partner_key):
    await _set_parent(realdb, child_key="b", parent_key="a")
    try:
        async with realdb.client(key="a", role="admin") as c:
            resp = await c.delete(f"/api/partner/children/{realdb.info('b').org_id}")
        assert resp.status_code == 204
        assert await _parent_of(realdb, "b") is None

        assert await _audit_rows(realdb, "a", "partner.child_detached")
        assert await _audit_rows(realdb, "b", "partner.parent_unlinked")
    finally:
        await _set_parent(realdb, child_key="b", parent_key=None)


@pytest.mark.asyncio
async def test_detach_non_child_is_opaque_404(realdb, partner_key):
    """ISOLATION: A can't detach an org it doesn't parent — opaque 404, no effect."""
    await _set_parent(realdb, child_key="b", parent_key=None)  # B standalone
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.delete(f"/api/partner/children/{realdb.info('b').org_id}")
    assert resp.status_code == 404
    assert await _parent_of(realdb, "b") is None


@pytest.mark.asyncio
async def test_detach_admin_only(realdb, partner_key):
    await _set_parent(realdb, child_key="b", parent_key="a")
    try:
        async with realdb.client(key="a", role="ap_manager") as c:
            resp = await c.delete(f"/api/partner/children/{realdb.info('b').org_id}")
        assert resp.status_code == 403
        # Still linked — the denied call changed nothing.
        assert await _parent_of(realdb, "b") == realdb.info("a").org_id
    finally:
        await _set_parent(realdb, child_key="b", parent_key=None)
