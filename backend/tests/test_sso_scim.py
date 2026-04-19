"""Unit tests for SSO + SCIM pure-function surfaces.

Covers config resolution, redirect-URI construction, SCIM filter parsing,
SCIM schema shapes. The full OIDC handshake (state → Redis → callback)
needs an integration harness with a real Redis + a fake IdP, which lives
separately.
"""

import pytest

# ---------- SSO config resolution -----------------------------------------


def test_resolve_sso_config_returns_none_when_missing():
    from app.services.sso import resolve_sso_config

    assert resolve_sso_config(None) is None
    assert resolve_sso_config({}) is None
    assert resolve_sso_config({"sso": {"enabled": False}}) is None


def test_resolve_sso_config_returns_none_when_disabled():
    from app.services.sso import resolve_sso_config

    cfg = {
        "sso": {
            "enabled": False,
            "discovery_url": "https://x.okta.com/.well-known/openid-configuration",
            "client_id": "a",
            "client_secret": "b",
        }
    }
    assert resolve_sso_config(cfg) is None


def test_resolve_sso_config_raises_when_partial():
    from app.services.sso import SSOConfigError, resolve_sso_config

    with pytest.raises(SSOConfigError):
        resolve_sso_config({"sso": {"enabled": True, "client_id": "a"}})


def test_resolve_sso_config_returns_full_config():
    from app.services.sso import resolve_sso_config

    cfg = {
        "sso": {
            "enabled": True,
            "provider": "okta",
            "discovery_url": "https://acme.okta.com/.well-known/openid-configuration",
            "client_id": "cid",
            "client_secret": "sec",
            "allowed_email_domains": ["acme.com"],
        }
    }
    resolved = resolve_sso_config(cfg)
    assert resolved is not None
    assert resolved.provider == "okta"
    assert resolved.client_id == "cid"
    assert resolved.allowed_email_domains == ["acme.com"]


# ---------- Per-tenant redirect URI ---------------------------------------


def test_redirect_uri_is_per_tenant():
    """Each tenant gets its own callback URL built from the tenant URL
    template — critical so the callback lands on the tenant origin."""
    from app.services.sso import redirect_uri

    acme = redirect_uri("acme")
    techflow = redirect_uri("techflow")
    assert acme != techflow
    assert "acme" in acme
    assert "techflow" in techflow
    assert acme.endswith("/login/sso-callback")


# ---------- SCIM token hashing --------------------------------------------


def test_scim_token_hashing_is_deterministic():
    from app.services.sso import generate_scim_token, hash_scim_token

    raw, digest = generate_scim_token()
    assert hash_scim_token(raw) == digest
    assert len(digest) == 64  # sha256 hex


def test_scim_token_generation_is_unique():
    from app.services.sso import generate_scim_token

    a_raw, a_digest = generate_scim_token()
    b_raw, b_digest = generate_scim_token()
    assert a_raw != b_raw
    assert a_digest != b_digest


# ---------- SCIM filter parser --------------------------------------------


def test_scim_filter_supports_expected_attributes():
    """Lock the filter surface Okta + Entra actually use."""
    from sqlalchemy import select

    from app.api.scim import _apply_filter
    from app.models.user import User

    base = select(User)
    # Each of these should NOT raise
    _apply_filter(base, 'userName eq "a@b.com"')
    _apply_filter(base, 'emails eq "a@b.com"')
    _apply_filter(base, 'externalId eq "abc123"')
    _apply_filter(base, "active eq true")
    _apply_filter(base, "active eq false")


def test_scim_filter_rejects_unsupported():
    """Unsupported filters must raise a SCIM-compliant 400 (invalidFilter)."""
    from fastapi import HTTPException
    from sqlalchemy import select

    from app.api.scim import _apply_filter
    from app.models.user import User

    base = select(User)
    with pytest.raises(HTTPException) as exc:
        _apply_filter(base, 'phoneNumber eq "555-0100"')
    assert exc.value.status_code == 400


# ---------- SCIM schema contracts -----------------------------------------


def test_scim_user_schema_required_fields():
    """Lock the outbound SCIM User schema — IdPs validate against this."""
    from app.schemas.scim import USER_SCHEMA, SCIMUser

    user = SCIMUser(
        id="uuid-123",
        userName="x@example.com",
    )
    data = user.model_dump()
    assert USER_SCHEMA in data["schemas"]
    assert data["userName"] == "x@example.com"
    # active defaults to True per SCIM convention
    assert data["active"] is True


def test_scim_list_response_envelope():
    """/Users GET wraps results in a ListResponse — not a bare array."""
    from app.schemas.scim import LIST_RESPONSE_SCHEMA, SCIMListResponse, SCIMUser

    resp = SCIMListResponse(
        totalResults=1,
        itemsPerPage=1,
        Resources=[SCIMUser(id="u1", userName="x@y.com")],
    )
    data = resp.model_dump()
    assert LIST_RESPONSE_SCHEMA in data["schemas"]
    assert data["totalResults"] == 1
    assert data["startIndex"] == 1
    assert isinstance(data["Resources"], list)


def test_scim_error_envelope():
    from app.schemas.scim import ERROR_SCHEMA, SCIMError

    err = SCIMError(status="404", detail="not found")
    data = err.model_dump()
    assert ERROR_SCHEMA in data["schemas"]
    assert data["status"] == "404"


def test_scim_patch_op_shape():
    from app.schemas.scim import SCIMPatchOp, SCIMPatchRequest

    req = SCIMPatchRequest(
        Operations=[SCIMPatchOp(op="replace", path="active", value=False)]
    )
    assert req.Operations[0].op == "replace"
    assert req.Operations[0].path == "active"


# ---------- auth-sso config endpoint shape --------------------------------


def test_sso_config_public_omits_secrets():
    """The unauthenticated /api/auth/sso/config endpoint must NEVER leak
    client_secret or the SCIM bearer — it's reachable without auth."""
    from app.api.auth_sso import SSOConfigPublic

    fields = set(SSOConfigPublic.model_fields.keys())
    # Allowed fields only
    assert fields == {"enabled", "provider"}
