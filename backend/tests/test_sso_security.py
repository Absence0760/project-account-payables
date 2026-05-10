"""SSO security-property tests — OIDC CSRF / replay protection.

`test_sso_scim.py` covers config resolution + token shape; these tests
pin the security gates between the IdP redirect and the JWT mint.

State + nonce together close the two OIDC replay attacks:
  - State: a 24-byte random string bound to the user's pending login.
    Single-use, expires in `sso_state_ttl_seconds`. Without it, an
    attacker can forge a callback request and land authenticated as
    themselves on the victim's session (CSRF).
  - Nonce: bound to the ID token. Validates that the token the IdP
    returned matches the request we made. Without it, an attacker
    can replay a captured ID token from a different session.

Tests:
  - State is single-use (consume_state deletes the binding)
  - Unknown / tampered state raises SSOValidationError
  - State / nonce are URL-safe and long enough to resist brute force
  - State carries the tenant binding so a leaked state on tenant A
    can't redirect into tenant B
  - validate_id_token rejects a token whose nonce doesn't match
  - SCIM tokens are hashed at rest (sha256), never plaintext
  - SCIM tokens are unique across mintings
"""

from __future__ import annotations

import json
import time

import pytest


class _FakeRedis:
    def __init__(self):
        self.store: dict[str, tuple[float, bytes]] = {}

    async def setex(self, key, ttl, value):
        expiry = time.time() + ttl
        self.store[key] = (
            expiry,
            value if isinstance(value, bytes) else value.encode("utf-8"),
        )

    async def get(self, key):
        item = self.store.get(key)
        if not item:
            return None
        expiry, value = item
        if expiry < time.time():
            del self.store[key]
            return None
        return value

    async def delete(self, key):
        self.store.pop(key, None)


@pytest.fixture
def fake_redis(monkeypatch):
    fake = _FakeRedis()

    async def _get_redis():
        return fake

    monkeypatch.setattr("app.services.sso.get_redis", _get_redis)
    return fake


# ---------------------------------------------------------------------------
# State binding — CSRF protection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_state_is_single_use(fake_redis):
    """`consume_state` must delete the binding so a second callback
    with the same state fails. Otherwise an attacker who intercepts
    the redirect URL can replay it indefinitely."""
    from app.services import sso

    state, _nonce = await sso.create_state("acme")

    bound = await sso.consume_state(state)
    assert bound["tenant"] == "acme"

    with pytest.raises(sso.SSOValidationError):
        await sso.consume_state(state)


@pytest.mark.asyncio
async def test_state_unknown_value_is_rejected(fake_redis):
    """A state value we never minted must raise — not return an empty
    dict or default to the seeded tenant. A regression that
    "auto-creates" a binding on lookup would defeat the gate."""
    from app.services import sso

    with pytest.raises(sso.SSOValidationError):
        await sso.consume_state("never-minted-state")


@pytest.mark.asyncio
async def test_state_carries_tenant_binding(fake_redis):
    """The state row must capture the tenant slug. Otherwise a
    callback could be redirected into a different tenant's SSO
    config — privilege escalation by mixed-tenant state."""
    from app.services import sso

    state, _nonce = await sso.create_state("techflow")
    bound = await sso.consume_state(state)
    assert bound["tenant"] == "techflow"


@pytest.mark.asyncio
async def test_state_carries_unique_nonce(fake_redis):
    """Every mint must produce a fresh nonce — otherwise two
    concurrent logins could share a nonce and an attacker who
    captured one could replay the other's ID token."""
    from app.services import sso

    s1, n1 = await sso.create_state("acme")
    s2, n2 = await sso.create_state("acme")
    assert s1 != s2
    assert n1 != n2


@pytest.mark.asyncio
async def test_state_and_nonce_are_url_safe_and_long_enough(fake_redis):
    """The values land in URL query strings — they must be URL-safe
    base64 (no padding, no `+`, no `/`). And they must carry enough
    entropy that an attacker can't brute-force the binding within
    the TTL window. 24 random bytes encoded ≈ 32 chars is the floor."""
    from app.services import sso

    state, nonce = await sso.create_state("acme")
    safe_chars = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
    for label, val in (("state", state), ("nonce", nonce)):
        assert len(val) >= 32, f"{label} too short: {len(val)} chars"
        assert set(val) <= safe_chars, f"{label} contains non-URL-safe chars"


@pytest.mark.asyncio
async def test_state_expires_after_configured_ttl(fake_redis, monkeypatch):
    """A stale state — past `sso_state_ttl_seconds` — must be rejected
    just like an unknown one. Tamper with the stored expiry to
    simulate the TTL lapse."""
    from app.services import sso

    state, _ = await sso.create_state("acme")
    key = f"sso:state:{state}"
    expiry, value = fake_redis.store[key]
    fake_redis.store[key] = (time.time() - 1, value)  # already expired

    with pytest.raises(sso.SSOValidationError):
        await sso.consume_state(state)


@pytest.mark.asyncio
async def test_state_does_not_leak_the_tenant_via_predictable_format(fake_redis):
    """A regression where the state itself encoded the tenant slug
    (`state = f"{slug}-{random}"`) would let an attacker who saw a
    redirect URL identify the tenant without consuming the state.
    Check that the state value is opaque random bytes."""
    from app.services import sso

    state, _ = await sso.create_state("acme")
    # The slug must not appear anywhere in the state.
    assert "acme" not in state.lower()


# ---------------------------------------------------------------------------
# Stored payload integrity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_state_payload_carries_a_timestamp(fake_redis):
    """The serialized payload includes a `ts` field. Operators read
    it for incident response — a regression that dropped it would
    blind the post-incident timeline."""
    from app.services import sso

    state, _ = await sso.create_state("acme")
    raw = fake_redis.store[f"sso:state:{state}"][1].decode("utf-8")
    payload = json.loads(raw)
    assert "ts" in payload
    assert isinstance(payload["ts"], (int, float))


# ---------------------------------------------------------------------------
# SCIM bearer token — hash at rest
# ---------------------------------------------------------------------------


def test_scim_token_hash_is_sha256_hex():
    """The token shown to the admin is plaintext; the value stored in
    org settings must be a sha256 hex digest. A regression that
    persisted plaintext would mean a DB dump compromises every
    tenant's SCIM endpoint."""
    from app.services.sso import generate_scim_token

    raw, digest = generate_scim_token()
    assert raw != digest
    assert len(digest) == 64
    assert all(c in "0123456789abcdef" for c in digest)


def test_scim_token_generation_is_unique():
    """Two consecutive mintings must produce different plaintexts
    (and therefore different hashes). Reuse would let an attacker who
    learned one token guess subsequent ones."""
    from app.services.sso import generate_scim_token

    seen = {generate_scim_token()[0] for _ in range(20)}
    assert len(seen) == 20, "scim token generator returned duplicates"


def test_scim_token_hash_is_deterministic_for_the_same_plaintext():
    """`hash_scim_token` must be deterministic so the verify path
    (comparing the request's bearer hash to the stored hash) is just
    a string comparison. If it weren't deterministic, verify would
    have to use the slow `verify` path or fail."""
    from app.services.sso import hash_scim_token

    plaintext = "abc-123-test-token"
    assert hash_scim_token(plaintext) == hash_scim_token(plaintext)


# ---------------------------------------------------------------------------
# ID-token nonce check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_id_token_validate_rejects_wrong_nonce(monkeypatch):
    """The bound nonce from `consume_state` must equal the nonce in
    the IdP-returned ID token. Without this check, a captured ID
    token from a different session can be replayed against our
    callback."""
    from app.services import sso

    # Mock JWKS fetch + jose decode to isolate the nonce check.
    fake_claims = {
        "iss": "https://idp.test",
        "aud": "client-1",
        "exp": int(time.time()) + 300,
        "email": "user@x.test",
        "nonce": "the-real-nonce-from-the-token",
    }

    class _FakeClaims(dict):
        def validate(self):
            return None

    def fake_decode(*args, **kwargs):
        return _FakeClaims(fake_claims)

    async def fake_fetch_jwks(_url):
        return {"keys": []}

    monkeypatch.setattr(sso, "fetch_jwks", fake_fetch_jwks)
    monkeypatch.setattr(sso.jose_jwt, "decode", fake_decode)

    discovery = {"jwks_uri": "https://idp.test/jwks", "issuer": "https://idp.test"}
    with pytest.raises(sso.SSOValidationError, match="modified in transit"):
        await sso.validate_id_token(
            id_token="x.y.z",
            discovery_doc=discovery,
            client_id="client-1",
            expected_nonce="something-completely-different",
        )


@pytest.mark.asyncio
async def test_id_token_validate_accepts_matching_nonce(monkeypatch):
    """Positive control — when the nonce matches, the claims are
    returned. Without this, the previous test could pass for the
    wrong reason (everything rejected)."""
    from app.services import sso

    nonce = "correct-nonce-abc"
    fake_claims = {
        "iss": "https://idp.test",
        "aud": "client-1",
        "exp": int(time.time()) + 300,
        "email": "user@x.test",
        "nonce": nonce,
    }

    class _FakeClaims(dict):
        def validate(self):
            return None

    def fake_decode(*args, **kwargs):
        return _FakeClaims(fake_claims)

    async def fake_fetch_jwks(_url):
        return {"keys": []}

    monkeypatch.setattr(sso, "fetch_jwks", fake_fetch_jwks)
    monkeypatch.setattr(sso.jose_jwt, "decode", fake_decode)

    discovery = {"jwks_uri": "https://idp.test/jwks", "issuer": "https://idp.test"}
    claims = await sso.validate_id_token(
        id_token="x.y.z",
        discovery_doc=discovery,
        client_id="client-1",
        expected_nonce=nonce,
    )
    assert claims["email"] == "user@x.test"


# ---------------------------------------------------------------------------
# Redirect URI is per-tenant — prevents tenant-mixed callbacks
# ---------------------------------------------------------------------------


def test_redirect_uri_includes_tenant_slug():
    """The OIDC redirect URI registered with the IdP is per-tenant,
    so an IdP misconfigured for tenant A can't be reused to phish
    tenant B's users by sending them to a callback under A's
    domain that's bound to B's state. Pin the slug appears in the URI."""
    from app.services.sso import redirect_uri

    uri_acme = redirect_uri("acme")
    uri_tf = redirect_uri("techflow")
    assert uri_acme != uri_tf
    assert "acme" in uri_acme
    assert "techflow" in uri_tf
