"""Public programmatic API — API-key auth + /api/v1 read surface.

Covers:
  * token primitives (mint shape, hash, constant-time compare) — pure unit
  * mint → use happy path (admin mints a key, the key reads /api/v1/invoices)
  * revoked key is rejected (401)
  * bad / missing key is rejected (401, opaque body)
  * a v1 endpoint returns tenant-scoped data
  * a key for tenant A cannot read tenant B's data (cross-tenant isolation)
  * key management is admin-gated + mint returns the plaintext exactly once,
    list never leaks the hash/plaintext

The auth + cross-tenant tests need the real-Postgres harness (two real tenant
DBs + the real tenant-resolution chokepoint). The token-primitive tests are
pure and always run.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from app.models.api_key import ApiKey
from app.models.billing import Plan, Subscription
from app.models.invoice import Invoice, InvoiceStatus
from app.models.organization import Organization
from app.services.api_keys import (
    KEY_BRAND,
    PREFIX_LEN,
    constant_time_equals,
    generate_api_key,
    hash_api_key,
)

# ---------------------------------------------------------------------------
# Pure token-primitive tests (no DB).
# ---------------------------------------------------------------------------


def test_generate_api_key_shape():
    full, prefix, digest = generate_api_key()
    assert full.startswith(f"{KEY_BRAND}_")
    assert prefix == full[:PREFIX_LEN]
    assert digest == hash_api_key(full)
    assert len(digest) == 64  # sha256 hex
    # The plaintext is not derivable from prefix + digest.
    assert prefix != full
    assert digest != full


def test_generate_api_key_is_unique():
    keys = {generate_api_key()[0] for _ in range(50)}
    assert len(keys) == 50


def test_hash_is_stable_and_compare_constant_time():
    full, _, digest = generate_api_key()
    assert hash_api_key(full) == digest
    assert constant_time_equals(digest, hash_api_key(full)) is True
    assert constant_time_equals(digest, hash_api_key(full + "x")) is False


# ---------------------------------------------------------------------------
# Real-DB integration: management + v1 surface + cross-tenant isolation.
# ---------------------------------------------------------------------------


def _api_key_client(realdb, key: str, *, api_key: str | None = None) -> httpx.AsyncClient:
    """An ASGI client for the API-key surface — NO JWT, NO X-Tenant-Slug.

    The tenant is resolved from the API key itself. We still install the
    realdb dependency overrides (control DB) by constructing the standard
    client and then stripping the JWT/tenant headers; the api-key path reads
    the (overridden) control DB and resolves the tenant engine directly
    against the real test tenant DB.
    """
    # role=None → no Authorization header; we drop the tenant header too.
    c = realdb.client(key=key, role=None)
    c.headers.pop("X-Tenant-Slug", None)
    if api_key is not None:
        c.headers["X-API-Key"] = api_key
    return c


async def _seed_invoice(mk, org_id, *, number: str) -> uuid.UUID:
    inv_id = uuid.uuid4()
    async with mk() as s:
        s.add(
            Invoice(
                id=inv_id,
                organization_id=org_id,
                correlation_id=uuid.uuid4(),
                invoice_number=number,
                vendor_name="Acme",
                amount=Decimal("123.45"),
                status=InvoiceStatus.approved,
            )
        )
        await s.commit()
    return inv_id


async def _grant_public_api(realdb, key: str) -> None:
    """Give the tenant a live plan that includes the ``public_api`` entitlement.

    The ``/api/v1`` routes are plan-gated (``require_api_entitlement("public_api")``
    → 402 without a granting plan), so any test that exercises a metered v1 call
    must seed one. Mirrors ``tests/test_billing.py::_seed_plan/_seed_subscription``.
    """
    from sqlalchemy import delete

    org_id = realdb.info(key).org_id
    plan_id = uuid.uuid4()
    async with realdb.control_sessionmaker()() as s:
        # The fixture reuses org rows across tests and doesn't truncate billing;
        # a leftover live subscription trips uq_subscription_one_live_per_org.
        await s.execute(delete(Subscription).where(Subscription.organization_id == org_id))
        await s.commit()
    async with realdb.control_sessionmaker()() as s:
        s.add(
            Plan(
                id=plan_id,
                code=f"meter_test_{uuid.uuid4().hex[:8]}",
                name="Meter Test",
                monthly_price=Decimal("49.00"),
                currency="USD",
                entitlements={"public_api": True},
                trial_days=14,
            )
        )
        s.add(
            Subscription(
                id=uuid.uuid4(),
                organization_id=org_id,
                plan_id=plan_id,
                status="active",
                current_period_start=datetime(2026, 6, 1, tzinfo=UTC),
                current_period_end=datetime(2026, 6, 30, tzinfo=UTC),
            )
        )
        await s.commit()


async def _mint_key(realdb, key: str, name: str = "ci-key") -> tuple[str, dict]:
    """Mint a key via the admin JWT management endpoint; return (plaintext, body)."""
    async with realdb.client(key=key, role="admin") as c:
        resp = await c.post("/api/api-keys", json={"name": name})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return body["key"], body


@pytest.mark.asyncio
async def test_mint_then_use_happy_path(realdb):
    mk = realdb.sessionmaker("a")
    inv_id = await _seed_invoice(mk, realdb.info("a").org_id, number="INV-A1")

    plaintext, body = await _mint_key(realdb, "a")
    # Mint returns the plaintext exactly once + metadata (no hash anywhere).
    assert plaintext.startswith(f"{KEY_BRAND}_")
    assert "key_hash" not in body["api_key"]
    assert body["api_key"]["scopes"] == ["read"]
    assert body["api_key"]["key_prefix"] == plaintext[:PREFIX_LEN]

    async with _api_key_client(realdb, "a", api_key=plaintext) as c:
        resp = await c.get("/api/v1/invoices")
        assert resp.status_code == 200, resp.text
        listed = resp.json()
        ids = {row["id"] for row in listed["data"]}
        assert str(inv_id) in ids
        # Money serialised as an exact string (public contract).
        row = next(r for r in listed["data"] if r["id"] == str(inv_id))
        assert row["amount"] == "123.45"

        detail = await c.get(f"/api/v1/invoices/{inv_id}")
        assert detail.status_code == 200
        assert detail.json()["invoice_number"] == "INV-A1"


@pytest.mark.asyncio
async def test_missing_and_bad_key_rejected(realdb):
    # No key at all.
    async with _api_key_client(realdb, "a") as c:
        resp = await c.get("/api/v1/invoices")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid API key"

    # Garbage key.
    async with _api_key_client(realdb, "a", api_key="ap_live_not-a-real-key") as c:
        resp = await c.get("/api/v1/invoices")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid API key"


@pytest.mark.asyncio
async def test_revoked_key_rejected(realdb):
    plaintext, body = await _mint_key(realdb, "a")
    key_id = body["api_key"]["id"]

    # Works before revoke.
    async with _api_key_client(realdb, "a", api_key=plaintext) as c:
        assert (await c.get("/api/v1/invoices")).status_code == 200

    # Revoke via admin management endpoint.
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.delete(f"/api/api-keys/{key_id}")
    assert resp.status_code == 200
    assert resp.json()["revoked_at"] is not None

    # Now rejected.
    async with _api_key_client(realdb, "a", api_key=plaintext) as c:
        resp = await c.get("/api/v1/invoices")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_cross_tenant_key_cannot_read_other_tenant(realdb):
    mk_a = realdb.sessionmaker("a")
    mk_b = realdb.sessionmaker("b")
    inv_a = await _seed_invoice(mk_a, realdb.info("a").org_id, number="INV-ONLY-A")
    inv_b = await _seed_invoice(mk_b, realdb.info("b").org_id, number="INV-ONLY-B")

    # Mint a key for tenant A only.
    plaintext_a, _ = await _mint_key(realdb, "a")

    async with _api_key_client(realdb, "a", api_key=plaintext_a) as c:
        listed = (await c.get("/api/v1/invoices")).json()
        ids = {row["id"] for row in listed["data"]}
        # Sees its own invoice, never tenant B's.
        assert str(inv_a) in ids
        assert str(inv_b) not in ids

        # Direct fetch of tenant B's invoice id is a 404 (not found in A's DB),
        # never leaks its existence.
        resp = await c.get(f"/api/v1/invoices/{inv_b}")
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_management_is_admin_gated(realdb):
    # A clerk JWT cannot mint or list keys.
    async with realdb.client(key="a", role="ap_clerk") as c:
        assert (await c.post("/api/api-keys", json={"name": "x"})).status_code == 403
        assert (await c.get("/api/api-keys")).status_code == 403


@pytest.mark.asyncio
async def test_list_returns_metadata_only(realdb):
    plaintext, _ = await _mint_key(realdb, "a", name="reporting-bot")
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.get("/api/api-keys")
    assert resp.status_code == 200
    rows = resp.json()
    assert any(r["name"] == "reporting-bot" for r in rows)
    for r in rows:
        # Never the secret material.
        assert "key_hash" not in r
        assert "key" not in r
        # The full plaintext must not be reconstructable from list output.
        assert plaintext not in str(r)


# ---------------------------------------------------------------------------
# Per-key usage metering (feeds billing).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metered_call_increments_usage(realdb):
    """Each authenticated /api/v1 call bumps the per-key, per-day counter, and the
    admin usage endpoint reports the running total — counts only, no key material."""
    mk = realdb.sessionmaker("a")
    await _seed_invoice(mk, realdb.info("a").org_id, number="INV-USAGE-1")
    await _grant_public_api(realdb, "a")

    plaintext, body = await _mint_key(realdb, "a", name="metered-bot")
    key_id = body["api_key"]["id"]

    # Three programmatic reads (list + detail-ish + list again).
    async with _api_key_client(realdb, "a", api_key=plaintext) as c:
        for _ in range(3):
            assert (await c.get("/api/v1/invoices")).status_code == 200

    # Admin reads the meter.
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.get(f"/api/api-keys/{key_id}/usage")
    assert resp.status_code == 200, resp.text
    usage = resp.json()
    assert usage["api_key_id"] == key_id
    assert usage["key_prefix"] == plaintext[:PREFIX_LEN]
    assert usage["total_requests"] == 3
    assert usage["window_requests"] == 3
    # Aggregate, not per-request: a single day row holding the running count.
    assert len(usage["daily"]) == 1
    assert usage["daily"][0]["request_count"] == 3
    # last_used_at was stamped by the same auth path.
    assert usage["last_used_at"] is not None
    # No key material leaks anywhere in the response.
    assert "key_hash" not in str(usage)
    assert plaintext not in str(usage)


@pytest.mark.asyncio
async def test_usage_endpoint_is_admin_gated(realdb):
    plaintext, body = await _mint_key(realdb, "a")
    key_id = body["api_key"]["id"]
    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await c.get(f"/api/api-keys/{key_id}/usage")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_usage_endpoint_other_tenant_key_is_404(realdb):
    # Mint a key in tenant A; tenant B's admin must not read its usage.
    _, body = await _mint_key(realdb, "a")
    key_id = body["api_key"]["id"]
    async with realdb.client(key="b", role="admin") as c:
        resp = await c.get(f"/api/api-keys/{key_id}/usage")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_failed_usage_write_does_not_break_auth(monkeypatch):
    """Metering is best-effort: if the per-day usage write blows up, the API-key
    auth dependency must STILL resolve a principal (the meter is observability,
    not auth). Pure unit test with a stub control session — no realdb harness —
    so it deterministically pins the swallow-and-continue contract.
    """
    import app.api.deps as deps
    from app.api.deps import ApiKeyPrincipal, get_api_key_principal
    from app.services.api_keys import generate_api_key

    full_key, prefix, digest = generate_api_key()
    org_id = uuid.uuid4()
    key_id = uuid.uuid4()

    matched = ApiKey(
        id=key_id,
        organization_id=org_id,
        name="stub",
        key_prefix=prefix,
        key_hash=digest,
        scopes=["read"],
        revoked_at=None,
    )
    org = Organization(id=org_id, name="Stub", slug="stub", db_name="ap_stub")

    rollback_calls = {"n": 0}

    class _StubResult:
        def scalars(self):
            return self

        def all(self):
            return [matched]

    class _StubSession:
        async def execute(self, *_a, **_k):
            # First call: the prefix SELECT in get_api_key_principal.
            return _StubResult()

        async def get(self, _model, _id):
            return org

        async def commit(self):  # pragma: no cover - not reached on the failure path
            pass

        async def rollback(self):
            rollback_calls["n"] += 1

    # Make the meter write blow up; the auth path must swallow it.
    async def _boom(*_a, **_k):
        raise RuntimeError("simulated meter failure")

    monkeypatch.setattr(deps, "_record_api_key_usage", _boom)

    principal = await get_api_key_principal(x_api_key=full_key, db=_StubSession())

    # Auth still succeeded — a valid principal for the org.
    assert isinstance(principal, ApiKeyPrincipal)
    assert principal.api_key_id == key_id
    assert principal.organization_id == org_id
    assert "read" in principal.scopes
    # The best-effort block caught the failure and rolled back (never raised).
    assert rollback_calls["n"] == 1
