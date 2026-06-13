"""SSO helpers — OIDC config resolution, state/nonce management, token exchange.

One generic OIDC flow covers both Okta and Microsoft Entra (and any other
OIDC-compliant IdP) because all the provider-specific data comes from the
discovery document. Tenant-scoped config lives on Organization.settings.sso:

    {
      "enabled": true,
      "provider": "okta" | "entra" | "oidc",     # label only, drives UI copy
      "discovery_url": "https://<tenant>.okta.com/.well-known/openid-configuration",
      "client_id": "...",
      "client_secret": "...",                    # encrypted at rest via pg
      "scim_bearer_hash": "<sha256 hex>",        # per-tenant SCIM API token
      "allowed_email_domains": ["acme.com"]      # optional JIT allowlist
    }
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import httpx
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet

from app.config import settings
from app.redis import get_redis

logger = logging.getLogger(__name__)

STATE_PREFIX = "sso:state:"
DISCOVERY_CACHE_PREFIX = "sso:discovery:"
JWKS_CACHE_PREFIX = "sso:jwks:"
# Discovery / JWKS caches — safe to cache for a day. Both providers rotate
# signing keys on the order of weeks, so a day is conservative.
DISCOVERY_CACHE_TTL = 86400

# OIDC ID tokens are asymmetrically signed (RFC 7518). Pin verification to the
# asymmetric algorithm set so a forged token can't downgrade the signature to
# HMAC — the classic alg-confusion attack, where an attacker signs with the
# IdP's *public* key bytes as an HMAC secret — or to `alg:none`. joserfc raises
# UnsupportedAlgorithmError (a JoseError) for any header `alg` outside this set,
# so the catch below turns that into a generic rejection. Never add HS*/none.
ID_TOKEN_ALGORITHMS = [
    "RS256",
    "RS384",
    "RS512",
    "ES256",
    "ES384",
    "ES512",
    "PS256",
    "PS384",
    "PS512",
    "EdDSA",
]


class SSOConfigError(ValueError):
    """Raised when a tenant's SSO config is missing or invalid."""


class SSOValidationError(ValueError):
    """Raised when the IdP response fails validation (bad state, bad token)."""


@dataclass
class ResolvedSSOConfig:
    provider: str
    discovery_url: str
    client_id: str
    client_secret: str
    allowed_email_domains: list[str]


# The settings.sso block is shared by both SSO protocols, discriminated by a
# `protocol` key. Absent / "oidc" => the existing OIDC path (back-compat);
# "saml" => the SAML SP path. resolve_sso_config and resolve_saml_config each
# return None for the other protocol so neither can be driven by the wrong one.
SAML_PROTOCOL = "saml"


def _settings_protocol(sso: dict) -> str:
    return (sso.get("protocol") or "oidc").lower()


def resolve_sso_config(org_settings: dict | None) -> ResolvedSSOConfig | None:
    """Pull + validate the OIDC SSO block from Organization.settings. Returns
    None if OIDC SSO isn't configured for this tenant (incl. when the tenant is
    configured for SAML instead)."""
    if not org_settings:
        return None
    sso = org_settings.get("sso") or {}
    if not sso.get("enabled"):
        return None
    if _settings_protocol(sso) == SAML_PROTOCOL:
        # SAML tenant — resolved via resolve_saml_config, not the OIDC path.
        return None
    discovery = sso.get("discovery_url")
    client_id = sso.get("client_id")
    client_secret = sso.get("client_secret")
    if not (discovery and client_id and client_secret):
        raise SSOConfigError(
            "SSO is enabled but discovery_url/client_id/client_secret are missing."
        )
    return ResolvedSSOConfig(
        provider=sso.get("provider") or "oidc",
        discovery_url=discovery,
        client_id=client_id,
        client_secret=client_secret,
        allowed_email_domains=list(sso.get("allowed_email_domains") or []),
    )


def redirect_uri(tenant_slug: str) -> str:
    """Build the per-tenant callback URL.

    Each tenant registers their Okta/Entra app with *their own* subdomain as
    the redirect URI — e.g. acme.app.com for tenant `acme`. That way the
    callback lands on the tenant origin and our localStorage JWT works
    without cross-origin hops. The tenant URL template in settings is the
    single source of truth for what that URL looks like.
    """
    template = settings.tenant_url_template or "http://{slug}.localhost:7777"
    base = template.replace("{slug}", tenant_slug).rstrip("/")
    return f"{base}{settings.sso_redirect_path}"


# ---------------------------------------------------------------------------
# SAML 2.0 (Service-Provider) config
# ---------------------------------------------------------------------------


@dataclass
class ResolvedSAMLConfig:
    provider: str
    idp_entity_id: str
    idp_sso_url: str
    # Trust anchor: the IdP's signing cert(s), normalized to bare base64 (PEM
    # armor + whitespace stripped). x509_certs[0] is primary; the rest support
    # zero-downtime IdP cert rotation. NEVER a cert embedded in the assertion.
    idp_x509_cert: str
    idp_x509_cert_multi: list[str] = field(default_factory=list)
    sp_entity_id: str = ""
    idp_slo_url: str | None = None
    allowed_email_domains: list[str] = field(default_factory=list)


def saml_sp_entity_id(tenant_slug: str) -> str:
    """Per-tenant SP EntityID (== SAML Audience the IdP must assert). Derived
    from the backend public URL so each tenant's IdP only trusts that tenant's
    SP. Admins may override via settings.sso.sp_entity_id."""
    base = settings.api_public_url.rstrip("/")
    return f"{base}/api/auth/saml/metadata?slug={tenant_slug}"


def saml_acs_url() -> str:
    """Static Assertion Consumer Service URL the IdP POST-binds the SAMLResponse
    to. One ACS serves every tenant; the tenant is recovered from the
    server-minted RelayState, never from this URL. The IdP-asserted Destination
    is validated against this exact value."""
    base = settings.api_public_url.rstrip("/")
    return f"{base}/api/auth/saml/acs"


def _normalize_x509_cert(raw: str) -> str:
    """Strip PEM armor + whitespace and confirm the result is non-empty,
    valid base64. Raises SSOConfigError on empty/blank/garbage so a missing or
    malformed cert can NEVER reach python3-saml as "no cert => skip the
    signature check" — the load-bearing trust control."""
    if not raw or not raw.strip():
        raise SSOConfigError("SAML idp_x509_cert is empty.")
    lines = [ln.strip() for ln in raw.strip().splitlines() if ln.strip() and "-----" not in ln]
    b64 = "".join(lines) if lines else raw.strip()
    try:
        decoded = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SSOConfigError("SAML idp_x509_cert is not valid base64/PEM.") from exc
    if not decoded:
        raise SSOConfigError("SAML idp_x509_cert decoded to empty bytes.")
    return b64


def resolve_saml_config(org_settings: dict | None, tenant_slug: str) -> ResolvedSAMLConfig | None:
    """Pull + validate the SAML SSO block from Organization.settings. Returns
    None when SAML SSO isn't configured for this tenant (incl. OIDC tenants).
    Raises SSOConfigError when SAML is enabled but the IdP trust config is
    incomplete or the signing cert is missing/malformed."""
    if not org_settings:
        return None
    sso = org_settings.get("sso") or {}
    if not sso.get("enabled"):
        return None
    if _settings_protocol(sso) != SAML_PROTOCOL:
        return None

    idp_entity_id = sso.get("idp_entity_id")
    idp_sso_url = sso.get("idp_sso_url")
    raw_cert = sso.get("idp_x509_cert")
    if not (idp_entity_id and idp_sso_url and raw_cert):
        raise SSOConfigError(
            "SAML SSO is enabled but idp_entity_id/idp_sso_url/idp_x509_cert are missing."
        )

    cert = _normalize_x509_cert(raw_cert)
    cert_multi = [_normalize_x509_cert(c) for c in (sso.get("idp_x509_cert_multi") or [])]
    sp_entity_id = sso.get("sp_entity_id") or saml_sp_entity_id(tenant_slug)

    return ResolvedSAMLConfig(
        provider=sso.get("provider") or "saml",
        idp_entity_id=idp_entity_id,
        idp_sso_url=idp_sso_url,
        idp_x509_cert=cert,
        idp_x509_cert_multi=cert_multi,
        sp_entity_id=sp_entity_id,
        idp_slo_url=sso.get("idp_slo_url") or None,
        allowed_email_domains=list(sso.get("allowed_email_domains") or []),
    )


# ---------------------------------------------------------------------------
# Discovery + JWKS (cached in Redis)
# ---------------------------------------------------------------------------


async def fetch_discovery(discovery_url: str) -> dict[str, Any]:
    """Return the OIDC provider's discovery document, cached in Redis."""
    r = await get_redis()
    key = f"{DISCOVERY_CACHE_PREFIX}{hashlib.sha256(discovery_url.encode()).hexdigest()}"
    cached = await r.get(key)
    if cached:
        return json.loads(cached)

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(discovery_url)
        resp.raise_for_status()
        doc = resp.json()

    await r.setex(key, DISCOVERY_CACHE_TTL, json.dumps(doc))
    return doc


async def fetch_jwks(jwks_uri: str) -> dict[str, Any]:
    r = await get_redis()
    key = f"{JWKS_CACHE_PREFIX}{hashlib.sha256(jwks_uri.encode()).hexdigest()}"
    cached = await r.get(key)
    if cached:
        return json.loads(cached)

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(jwks_uri)
        resp.raise_for_status()
        jwks = resp.json()

    await r.setex(key, DISCOVERY_CACHE_TTL, json.dumps(jwks))
    return jwks


# ---------------------------------------------------------------------------
# State + nonce (CSRF / replay protection)
# ---------------------------------------------------------------------------


async def create_state(tenant_slug: str) -> tuple[str, str]:
    """Mint a state + nonce, store binding in Redis, return both.

    State defends the callback against CSRF; nonce defends the ID token
    against replay. Both are single-use and expire in ~10 minutes.
    """
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    r = await get_redis()
    payload = json.dumps({"tenant": tenant_slug, "nonce": nonce, "ts": time.time()})
    await r.setex(f"{STATE_PREFIX}{state}", settings.sso_state_ttl_seconds, payload)
    return state, nonce


async def consume_state(state: str) -> dict[str, Any]:
    """Look up + delete the state binding. Raises if state is unknown/expired."""
    r = await get_redis()
    key = f"{STATE_PREFIX}{state}"
    raw = await r.get(key)
    if not raw:
        raise SSOValidationError("Login session expired or was tampered with. Please try again.")
    await r.delete(key)
    return json.loads(raw)


# ---------------------------------------------------------------------------
# SAML RelayState + one-time token handoff
# ---------------------------------------------------------------------------

SAML_RELAYSTATE_PREFIX = "saml:relaystate:"
SAML_HANDOFF_PREFIX = "saml:handoff:"


async def store_saml_relay_state(state: str, tenant_slug: str, request_id: str) -> None:
    """Bind a SAML RelayState to its tenant AND the AuthnRequest ID, atomically,
    as a single single-use Redis record. The ACS recovers BOTH from the one
    consume — so InResponseTo can be enforced against the exact request that was
    issued, and the tenant is taken from server-minted state (never the IdP)."""
    r = await get_redis()
    payload = json.dumps({"tenant": tenant_slug, "request_id": request_id, "ts": time.time()})
    await r.setex(f"{SAML_RELAYSTATE_PREFIX}{state}", settings.sso_state_ttl_seconds, payload)


async def consume_saml_relay_state(state: str) -> dict[str, Any]:
    """Look up + delete the RelayState binding (single-use). Raises if unknown
    or expired. Returns {tenant, request_id}."""
    r = await get_redis()
    key = f"{SAML_RELAYSTATE_PREFIX}{state}"
    raw = await r.get(key)
    if not raw:
        raise SSOValidationError("Login session expired or was tampered with. Please try again.")
    await r.delete(key)
    return json.loads(raw)


async def create_saml_handoff(
    access_token: str, must_change_password: bool, tenant_slug: str
) -> str:
    """Stash a freshly-minted JWT behind a one-time code so the ACS can
    303-redirect WITHOUT putting the token in the URL. The SPA bridge POSTs the
    code to /exchange and gets the token in the response body — mirroring the
    OIDC POST-to-callback shape (token never transits a URL / Referer / history)."""
    code = secrets.token_urlsafe(32)
    r = await get_redis()
    payload = json.dumps(
        {
            "access_token": access_token,
            "must_change_password": must_change_password,
            "tenant": tenant_slug,
        }
    )
    await r.setex(f"{SAML_HANDOFF_PREFIX}{code}", settings.saml_handoff_ttl_seconds, payload)
    return code


async def consume_saml_handoff(code: str) -> dict[str, Any]:
    """Look up + delete the handoff record (single-use). Raises if expired."""
    r = await get_redis()
    key = f"{SAML_HANDOFF_PREFIX}{code}"
    raw = await r.get(key)
    if not raw:
        raise SSOValidationError("This login link has expired. Please sign in again.")
    await r.delete(key)
    return json.loads(raw)


def saml_bridge_url(tenant_slug: str) -> str:
    """Per-tenant SPA bridge route the ACS 303-redirects to after minting the
    one-time handoff code. Lands on the tenant origin so the stored JWT works
    without a cross-origin hop, exactly like the OIDC callback page."""
    template = settings.tenant_url_template or "http://{slug}.localhost:7777"
    base = template.replace("{slug}", tenant_slug).rstrip("/")
    return f"{base}{settings.saml_acs_path}"


# ---------------------------------------------------------------------------
# Authorize URL + token exchange
# ---------------------------------------------------------------------------


def build_authorize_url(
    discovery_doc: dict, client_id: str, state: str, nonce: str, tenant_slug: str
) -> str:
    endpoint = discovery_doc["authorization_endpoint"]
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri(tenant_slug),
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
    }
    return f"{endpoint}?{urlencode(params)}"


async def exchange_code_for_tokens(
    discovery_doc: dict,
    client_id: str,
    client_secret: str,
    code: str,
    tenant_slug: str,
) -> dict[str, Any]:
    """POST to the token endpoint with the auth code. Returns the token bundle.

    The redirect_uri here MUST exactly match the one sent during authorize —
    OIDC token exchange validates it as a defence against code-injection attacks.
    """
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            discovery_doc["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri(tenant_slug),
                "client_id": client_id,
                "client_secret": client_secret,
            },
            headers={"Accept": "application/json"},
        )
    if resp.status_code != 200:
        logger.warning("Token exchange failed: %s %s", resp.status_code, resp.text[:200])
        raise SSOValidationError("Identity provider rejected the login. Please try again.")
    return resp.json()


async def validate_id_token(
    id_token: str, discovery_doc: dict, client_id: str, expected_nonce: str
) -> dict[str, Any]:
    """Verify the ID token signature + standard claims. Returns decoded claims."""
    jwks = await fetch_jwks(discovery_doc["jwks_uri"])
    try:
        key_set = KeySet.import_key_set(jwks)
        # decode() verifies the signature against the JWKS, restricted to the
        # pinned asymmetric algorithms. The claims registry then enforces the
        # standard OIDC checks — issuer + audience match, and (via the built-in
        # exp validator) that the token has not expired.
        token = jwt.decode(id_token, key_set, algorithms=ID_TOKEN_ALGORITHMS)
        claims_registry = jwt.JWTClaimsRegistry(
            iss={"essential": True, "value": discovery_doc["issuer"]},
            aud={"essential": True, "value": client_id},
            exp={"essential": True},
        )
        claims_registry.validate(token.claims)
    except (JoseError, KeyError, TypeError) as exc:
        # JoseError covers signature / claim / algorithm failures. KeyError and
        # TypeError cover a malformed JWKS from the IdP (a JSON object without a
        # "keys" field, or a non-object) — KeySet.import_key_set raises those
        # bare, and without catching them the handler would 500 and leak the
        # JWKS URL + contents in the traceback. All fail closed to the same
        # generic rejection.
        logger.warning("ID token validation failed: %s", exc.__class__.__name__)
        raise SSOValidationError("Identity provider token could not be verified.") from exc

    claims = token.claims
    if claims.get("nonce") != expected_nonce:
        raise SSOValidationError("Login was modified in transit. Please try again.")

    return dict(claims)


# ---------------------------------------------------------------------------
# SCIM bearer token
# ---------------------------------------------------------------------------


def generate_scim_token() -> tuple[str, str]:
    """Mint a SCIM bearer token. Returns (plaintext, sha256_hex).

    Callers store ONLY the hex digest in org settings. The plaintext is shown
    to the admin once at generation time and never persisted.
    """
    raw = secrets.token_urlsafe(32)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return raw, digest


def hash_scim_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
