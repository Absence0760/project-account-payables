"""Pure unit tests for the partner link-code token (no DB / network).

Mirrors `test_email_action_token`-style coverage: build/verify round-trip,
fail-closed on an empty key, forgery / tamper rejection, purpose binding, and
expiry — the integrity guarantees the attach authorization boundary rests on.
"""

from __future__ import annotations

import uuid

from app.services.partner_link_token import build_link_code, verify_link_code

KEY = "unit-test-partner-key"


def test_round_trip():
    child = uuid.uuid4()
    code = build_link_code(child_org_id=child, signing_key=KEY, ttl_minutes=30)
    assert code is not None
    decoded = verify_link_code(code, KEY)
    assert decoded is not None
    assert decoded.child_org_id == child
    assert decoded.exp > 0
    assert decoded.jti


def test_empty_key_fails_closed_on_build_and_verify():
    child = uuid.uuid4()
    # No key → no code can be minted (feature off).
    assert build_link_code(child_org_id=child, signing_key="", ttl_minutes=30) is None
    # And a code minted under a real key never verifies under an empty key.
    code = build_link_code(child_org_id=child, signing_key=KEY, ttl_minutes=30)
    assert verify_link_code(code, "") is None


def test_wrong_key_is_rejected():
    code = build_link_code(child_org_id=uuid.uuid4(), signing_key=KEY, ttl_minutes=30)
    assert verify_link_code(code, "a-different-key") is None


def test_tampered_payload_is_rejected():
    code = build_link_code(child_org_id=uuid.uuid4(), signing_key=KEY, ttl_minutes=30)
    body, _, sig = code.rpartition(".")
    # Flip a byte in the signed body → signature no longer matches.
    forged_body = ("A" if body[0] != "A" else "B") + body[1:]
    assert verify_link_code(f"{forged_body}.{sig}", KEY) is None


def test_malformed_tokens_are_rejected_not_raised():
    for bad in [None, "", "no-dot", ".", "abc.", ".def", "not-base64.deadbeef"]:
        assert verify_link_code(bad, KEY) is None


def test_expired_code_is_rejected():
    child = uuid.uuid4()
    # Minted "now"; verify 31 minutes later against a 30-minute TTL.
    code = build_link_code(child_org_id=child, signing_key=KEY, ttl_minutes=30, now=1000.0)
    assert verify_link_code(code, KEY, now=1000.0 + 30 * 60 - 1) is not None
    assert verify_link_code(code, KEY, now=1000.0 + 31 * 60) is None


def test_distinct_jti_per_mint():
    child = uuid.uuid4()
    a = verify_link_code(build_link_code(child_org_id=child, signing_key=KEY, ttl_minutes=30), KEY)
    b = verify_link_code(build_link_code(child_org_id=child, signing_key=KEY, ttl_minutes=30), KEY)
    assert a.jti != b.jti


def test_wrong_purpose_token_is_rejected():
    # A token shaped like an email-action token (different purpose marker) must
    # not verify as a partner link code, even with a matching signature.
    import base64
    import hashlib
    import hmac
    import json

    payload = {"p": "something_else", "c": str(uuid.uuid4()), "exp": 9999999999, "jti": "x"}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    sig = hmac.new(KEY.encode(), body.encode("ascii"), hashlib.sha256).hexdigest()
    assert verify_link_code(f"{body}.{sig}", KEY) is None
