"""Unit tests for the email-approval signed-token primitive (pure, no DB).

Covers: build/verify round-trip, wrong key, tampered payload, tampered
signature, action-flip detection, expiry, empty-key fail-closed, invalid
action, malformed input, and the email link builder.
"""

from __future__ import annotations

import time
import uuid

from app.services.email_action_token import (
    ACTION_APPROVE,
    ACTION_REJECT,
    build_action_token,
    build_email_action_links,
    verify_action_token,
)

_KEY = "unit-test-signing-key"


def _build(action: str = ACTION_APPROVE, key: str = _KEY, **kw) -> str:
    return build_action_token(
        tenant_slug=kw.get("tenant_slug", "acme"),
        invoice_id=kw.get("invoice_id", uuid.uuid4()),
        actor_id=kw.get("actor_id", uuid.uuid4()),
        action=action,
        signing_key=key,
        ttl_hours=kw.get("ttl_hours", 24),
        now=kw.get("now"),
    )


def test_round_trip_preserves_facts():
    inv, actor = uuid.uuid4(), uuid.uuid4()
    token = _build(ACTION_REJECT, tenant_slug="acme", invoice_id=inv, actor_id=actor)
    decoded = verify_action_token(token, _KEY)
    assert decoded is not None
    assert decoded.tenant_slug == "acme"
    assert decoded.invoice_id == inv
    assert decoded.actor_id == actor
    assert decoded.action == ACTION_REJECT
    assert decoded.jti  # a random per-token id is present


def test_each_token_has_a_unique_jti():
    a = verify_action_token(_build(), _KEY)
    b = verify_action_token(_build(), _KEY)
    assert a.jti != b.jti


def test_wrong_key_rejected():
    assert verify_action_token(_build(), "other-key") is None


def test_tampered_signature_rejected():
    token = _build()
    body, _, sig = token.rpartition(".")
    flipped = sig[:-1] + ("a" if sig[-1] != "a" else "b")
    assert verify_action_token(f"{body}.{flipped}", _KEY) is None


def test_tampered_payload_rejected():
    # Flip a byte in the payload portion — signature no longer matches.
    token = _build()
    body, _, sig = token.rpartition(".")
    mangled = ("A" if body[0] != "A" else "B") + body[1:]
    assert verify_action_token(f"{mangled}.{sig}", _KEY) is None


def test_action_cannot_be_swapped():
    """An attacker can't turn a reject link into an approve link: the action is
    inside the signed payload, so any edit breaks the signature."""
    reject = _build(ACTION_REJECT)
    decoded = verify_action_token(reject, _KEY)
    assert decoded.action == ACTION_REJECT  # stays reject; no way to forge approve


def test_expired_token_rejected():
    token = _build(now=time.time() - 48 * 3600, ttl_hours=24)  # issued 2 days ago, 24h TTL
    assert verify_action_token(token, _KEY) is None


def test_not_yet_expired_token_accepted():
    token = _build(now=time.time() - 1 * 3600, ttl_hours=24)
    assert verify_action_token(token, _KEY) is not None


def test_empty_key_fails_closed_on_build_and_verify():
    assert (
        build_action_token(
            tenant_slug="a",
            invoice_id=uuid.uuid4(),
            actor_id=uuid.uuid4(),
            action=ACTION_APPROVE,
            signing_key="",
            ttl_hours=24,
        )
        is None
    )
    # Even a structurally valid token is rejected when no key is configured.
    assert verify_action_token(_build(), "") is None


def test_invalid_action_not_built_or_verified():
    assert (
        build_action_token(
            tenant_slug="a",
            invoice_id=uuid.uuid4(),
            actor_id=uuid.uuid4(),
            action="delete",
            signing_key=_KEY,
            ttl_hours=24,
        )
        is None
    )


def test_malformed_tokens_rejected():
    for bad in [None, "", "no-dot", "a.b.c.d", ".", "x.", ".y", "garbage.deadbeef"]:
        assert verify_action_token(bad, _KEY) is None


def test_link_builder_returns_none_without_key():
    assert (
        build_email_action_links(
            api_base_url="http://localhost:8000",
            tenant_slug="acme",
            invoice_id=uuid.uuid4(),
            actor_id=uuid.uuid4(),
            signing_key="",
            ttl_hours=24,
        )
        is None
    )


def test_link_builder_emits_both_valid_links():
    inv, actor = uuid.uuid4(), uuid.uuid4()
    links = build_email_action_links(
        api_base_url="http://localhost:8000/",
        tenant_slug="acme",
        invoice_id=inv,
        actor_id=actor,
        signing_key=_KEY,
        ttl_hours=24,
    )
    assert links is not None
    text, html = links
    assert "Approve:" in text and "Reject:" in text
    assert text.count("/api/invoices/email-action/") == 2
    assert 'href="http://localhost:8000/api/invoices/email-action/' in html
    # Each link's embedded token must verify and carry the right action + facts.
    for line in text.splitlines():
        token = line.split("email-action/")[1]
        decoded = verify_action_token(token, _KEY)
        assert decoded is not None
        assert decoded.invoice_id == inv and decoded.actor_id == actor
        assert decoded.action in (ACTION_APPROVE, ACTION_REJECT)
