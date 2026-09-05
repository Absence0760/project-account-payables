"""Which WebAuthn Relying Party a passkey ceremony runs under — resolved per
request, from the host the browser is actually on.

A WebAuthn credential is bound to exactly ONE registrable domain (its RP ID).
``FEOH_WEBAUTHN_RP_ID`` is a single global, so before this module a tenant
served on a vanity host (``ap.acmecorp.com``) could neither register nor use a
passkey: the browser refuses to sign an RP ID that isn't the page's effective
domain or a registrable parent of it. TOTP and email OTP were unaffected, so it
read as a silently *reduced* second-factor menu rather than an error.

This module is the single owner of the ``(rp_id, origins)`` pair. Every ceremony
site — register begin/finish, authenticate begin/finish, step-up begin/verify —
resolves through it, so registration and authentication can't drift onto
different RP IDs (a credential registered under one RP ID is unusable under
another, which would look like an opaque signature failure).

Fail-closed, because ``Host`` is client-supplied
-------------------------------------------------
The request ``Host`` header is attacker-controlled. An RP ID derived from it
without validation would let an attacker who can get a victim's browser to a
host of their choosing steer a registration ceremony onto a domain they control.
So a host becomes an RP ID **only** when it is a custom domain the resolved
tenant has actually registered on ``settings.brand.custom_domains`` (the same
list ``app/tenant.py`` maps an inbound ``Host`` to a tenant slug with — one
normalizer, ``normalize_custom_domain``, shared). Anything else — an unknown
host, a forged one, another tenant's vanity domain — falls back to the global
``FEOH_WEBAUTHN_RP_ID`` / ``FEOH_WEBAUTHN_ORIGINS`` exactly as before. There is
no path from an unvalidated ``Host`` to an RP ID.

A host that already sits *under* the platform RP ID (``acme.localhost`` under
``localhost``, ``acme.app.example.com`` under ``app.example.com``) keeps the
platform RP ID even if it is also registered as a custom domain. That is the
conservative choice: the platform RP ID already covers it, so existing passkeys
keep working and nothing needs re-registering.

Origins
-------
For a registered custom domain the allowed-origin list is ``https://<host>``
plus the configured platform origins. The RP ID is the real binding (the
authenticator signs ``sha256(rp_id)`` into ``authenticatorData`` and the browser
will only produce it for a page whose origin is under that domain); the origin
list is a pre-screen on top. Keeping the platform entries means an operator can
still add an explicit dev origin (``http://ap.acme.localhost:7777``) without a
code change, which is what makes a vanity host exercisable on a laptop.

See ``docs/authentication.md`` § Passkeys on a custom domain.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.config import settings
from app.tenant import normalize_custom_domain

# Last-resort origin when `FEOH_WEBAUTHN_ORIGINS` is empty — the dev SPA.
DEFAULT_ORIGINS: tuple[str, ...] = ("http://localhost:7777",)
# Last-resort RP ID when `FEOH_WEBAUTHN_RP_ID` is blank. Matches the config
# default; a blank RP ID would make every ceremony fail, so never resolve to "".
DEFAULT_RP_ID = "localhost"

RP_SOURCE_PLATFORM = "platform"
RP_SOURCE_CUSTOM_DOMAIN = "custom_domain"


@dataclass(frozen=True)
class RelyingParty:
    """The effective Relying Party for one ceremony.

    ``rp_id`` is what the credential is (or was) bound to; ``origins`` is the
    allowlist the ceremony's ``clientDataJSON.origin`` is screened against;
    ``source`` records WHY, so a caller can explain the choice without
    re-deriving it.
    """

    rp_id: str
    origins: tuple[str, ...]
    source: str

    @property
    def is_custom_domain(self) -> bool:
        return self.source == RP_SOURCE_CUSTOM_DOMAIN


def platform_rp_id() -> str:
    """The globally configured RP ID, normalized to a bare lowercase host."""
    raw = (settings.webauthn_rp_id or "").strip().lower()
    return raw or DEFAULT_RP_ID


def platform_origins() -> tuple[str, ...]:
    """The configured allowed origins (comma-separated), dev default if empty."""
    raw = settings.webauthn_origins or ""
    origins = tuple(o.strip() for o in raw.split(",") if o.strip())
    return origins or DEFAULT_ORIGINS


def platform_relying_party() -> RelyingParty:
    """The global RP — the answer for every host that isn't a registered
    tenant vanity domain. This is the fail-closed fallback."""
    return RelyingParty(
        rp_id=platform_rp_id(),
        origins=platform_origins(),
        source=RP_SOURCE_PLATFORM,
    )


def _is_under_platform_rp(host: str) -> bool:
    base = platform_rp_id()
    return host == base or host.endswith("." + base)


def requires_tenant_domain_lookup(host: str | None) -> bool:
    """Would resolving this host need the tenant's registered-domain list?

    ``False`` for an absent host and for anything already under the platform RP
    ID — those resolve to the platform RP with no DB read at all, which keeps
    the common (subdomain) path free of an extra org load. Callers use this to
    decide whether to fetch the org before calling ``resolve_relying_party``.
    """
    normalized = normalize_custom_domain(host)
    return normalized is not None and not _is_under_platform_rp(normalized)


def registered_custom_domains(org_settings: dict | None) -> tuple[str, ...]:
    """Normalized vanity hostnames the tenant has registered.

    Reads ``settings.brand.custom_domains`` with the same tolerance as
    ``api/organization._resolve_custom_domains`` — a missing or malformed block
    yields an empty tuple, never an exception, so a bad settings blob degrades
    to "no custom domain" (the platform RP) rather than breaking sign-in.
    """
    if not isinstance(org_settings, dict):
        return ()
    brand = org_settings.get("brand")
    if not isinstance(brand, dict):
        return ()
    raw = brand.get("custom_domains")
    if not isinstance(raw, list):
        return ()
    out: list[str] = []
    for entry in raw:
        if not isinstance(entry, str):
            continue
        normalized = normalize_custom_domain(entry)
        if normalized:
            out.append(normalized)
    return tuple(out)


def resolve_relying_party(*, host: str | None, org_settings: dict | None) -> RelyingParty:
    """The effective RP for a ceremony arriving on ``host`` for this tenant.

    ``org_settings`` MUST be the settings of the org that owns the account the
    ceremony is for — that is what makes another tenant's vanity domain
    unusable here: it simply isn't in this org's list, so it falls back to the
    platform RP instead of becoming the RP ID.
    """
    normalized = normalize_custom_domain(host)
    if normalized is None or _is_under_platform_rp(normalized):
        return platform_relying_party()
    if normalized not in registered_custom_domains(org_settings):
        # Unknown / forged / another tenant's host. Fail closed to the global
        # config — an unvalidated Host never becomes an RP ID.
        return platform_relying_party()
    return RelyingParty(
        rp_id=normalized,
        origins=(f"https://{normalized}", *platform_origins()),
        source=RP_SOURCE_CUSTOM_DOMAIN,
    )


def effective_rp_id(stored_rp_id: str | None) -> str:
    """The RP ID a stored credential is bound to.

    ``NULL`` means the row predates per-host resolution (or was written by an
    old worker mid-deploy); migration ``0091`` backfills those to the configured
    global, which is provably what they were registered under, so NULL resolves
    the same way.
    """
    return (stored_rp_id or "").strip().lower() or platform_rp_id()


def usable_under(stored_rp_id: str | None, rp: RelyingParty) -> bool:
    """Can this stored credential be presented in a ceremony under ``rp``?

    A passkey is bound to one registrable domain — so no, when the account's
    passkey was registered on a different host. That is WebAuthn working as
    designed, not a bug to code around; the callers surface it as "this passkey
    belongs to <host>" rather than an opaque signature failure.
    """
    return effective_rp_id(stored_rp_id) == rp.rp_id


def other_rp_ids(stored_rp_ids: Sequence[str | None], rp: RelyingParty) -> tuple[str, ...]:
    """Distinct RP IDs among these credentials that are NOT usable under ``rp``.

    Feeds the "you have a passkey, but it belongs to <host>" message. Ordered by
    first appearance so the message is deterministic.
    """
    seen: list[str] = []
    for stored in stored_rp_ids:
        resolved = effective_rp_id(stored)
        if resolved != rp.rp_id and resolved not in seen:
            seen.append(resolved)
    return tuple(seen)
