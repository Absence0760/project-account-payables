"""The `Organization.settings.chat_notifications` block — its single owner.

That block carries the org's outbound chat-notification config *and*
``webhook_url``, which is the **credential** for both real providers. A Slack
or Microsoft Teams incoming-webhook URL is a bearer capability in a string:
anyone holding it can post arbitrary content into the customer's approval
channel, forever, with no further authentication. Three rules follow, and this
module is where they live so no caller has to re-derive them:

1. **It is write-only.** :func:`safe_status` is the only projection of the block
   that leaves the backend, and it reports *whether* a URL is configured plus
   its bare hostname — never the path, query or fragment, which is where every
   provider puts the token. Set it, replace it, remove it; never read it back.
2. **A config write must preserve it.** ``enabled`` / ``provider`` / ``events``
   are edited on a different cadence than the credential, so
   :func:`apply_config` carries ``webhook_url`` forward untouched. (A naive
   whole-block replace is exactly how the branding endpoint once wiped a
   tenant's ``custom_domains``.)
3. **Nothing derived from it may reach a log, an error body, or an audit row**
   beyond the hostname. :func:`webhook_host` is the ONLY sanctioned derivation,
   and it exists because "which host does our approval channel post to?" is the
   one question an admin genuinely needs answered during an incident — and it
   is answerable without handing back the token.

Everything here is pure: no DB, no network, no I/O. The router
(`api/organization.py`) does the persistence + the audit write; the dispatcher
(`services/notification_dispatch.py`) does the send.
"""

from __future__ import annotations

from urllib.parse import urlsplit

# Keys the block may carry. Anything else a caller persisted is preserved on a
# config write (forward-compat) but never interpreted here.
WEBHOOK_URL_KEY = "webhook_url"


class ChatConfigError(ValueError):
    """A supplied chat-notification config value is not acceptable.

    Carries a caller-safe message only — never a URL, never a credential.
    """


def coerce_chat_config(raw: object) -> dict:
    """Return ``raw`` as a config dict, tolerating a missing / malformed block.

    A persisted-but-now-invalid block must never break a read or a send, which
    mirrors ``organization._resolve_brand`` and the dispatcher's own defensive
    read.
    """
    return dict(raw) if isinstance(raw, dict) else {}


def webhook_host(url: object) -> str | None:
    """The bare hostname of a chat webhook URL, or ``None``.

    **This is the only value derived from the credential that may leave the
    backend or enter the audit trail.** The token lives in the path (Slack:
    ``/services/T…/B…/<token>``; Teams: ``/webhookb2/<guid>@<guid>/…``) or the
    query, and none of that is returned here — only ``hostname``, lowercased by
    ``urlsplit``. A non-string, empty, or unparseable value yields ``None``
    rather than raising, so a corrupt settings row can't break a read.
    """
    if not isinstance(url, str) or not url.strip():
        return None
    try:
        host = urlsplit(url.strip()).hostname
    except ValueError:
        return None
    return host or None


def is_webhook_configured(config: dict) -> bool:
    """Whether the org has a non-empty webhook URL persisted."""
    raw = config.get(WEBHOOK_URL_KEY)
    return isinstance(raw, str) and bool(raw.strip())


def normalize_provider(provider: object, *, supported: list[str]) -> str:
    """Validate a provider key against the live adapter registry.

    Reads the registry rather than a second hardcoded tuple, so registering a
    new chat adapter widens the API automatically instead of needing this list
    updated too (the drift that produces a provider the UI can't select).
    """
    value = (provider or "").strip() if isinstance(provider, str) else ""
    if not value:
        raise ChatConfigError("provider is required")
    if value not in supported:
        raise ChatConfigError(f"Unsupported chat provider '{value}'; valid: {sorted(supported)}")
    return value


def normalize_events(events: object, *, supported: tuple[str, ...]) -> dict[str, bool]:
    """Validate + coerce the per-event toggle map.

    An unknown event key is refused rather than silently stored: a typo would
    otherwise persist as a toggle that reads as configured in the UI and does
    nothing, which is the "silently ignored config" class this repo has already
    been bitten by (an unrecognised workflow step type, `decisions §32`).
    """
    if events is None:
        return {}
    if not isinstance(events, dict):
        raise ChatConfigError("events must be an object of {event_type: bool}")
    unknown = [k for k in events if k not in supported]
    if unknown:
        raise ChatConfigError(
            f"unknown chat event type(s): {', '.join(sorted(unknown))}. "
            f"Allowed: {', '.join(supported)}"
        )
    return {k: bool(v) for k, v in events.items()}


def apply_config(
    config: dict,
    *,
    enabled: bool,
    provider: str,
    events: dict[str, bool],
) -> dict:
    """Return a NEW block with the non-credential settings replaced.

    ``webhook_url`` (and any other key already persisted) is carried forward
    untouched — the credential is managed only by :func:`apply_webhook_url`, so
    saving the provider/event toggles can never drop it.
    """
    updated = dict(config)
    updated["enabled"] = bool(enabled)
    updated["provider"] = provider
    updated["events"] = dict(events)
    return updated


def apply_webhook_url(config: dict, url: str | None) -> dict:
    """Return a NEW block with the webhook credential replaced or removed.

    ``None`` / empty **removes** the key outright rather than storing an empty
    string, so ``is_webhook_configured`` and the adapters' own fail-closed check
    agree on a single representation of "no credential".

    There is no overlap window here, deliberately — see
    ``PUT /api/organization/chat-notifications/webhook`` for why a *destination*
    has nothing to overlap.
    """
    updated = dict(config)
    cleaned = (url or "").strip()
    if cleaned:
        updated[WEBHOOK_URL_KEY] = cleaned
    else:
        updated.pop(WEBHOOK_URL_KEY, None)
    return updated


def safe_status(config: dict) -> dict:
    """The credential-free projection of the block, for API responses.

    Returns ``enabled`` / ``provider`` / ``events`` verbatim plus
    ``webhook_configured`` + ``webhook_host``. The URL itself is structurally
    unable to escape through this function — callers building a response MUST
    go through it rather than reaching into the raw settings dict.
    """
    return {
        "enabled": bool(config.get("enabled")),
        "provider": config.get("provider") if isinstance(config.get("provider"), str) else None,
        "events": {k: bool(v) for k, v in (config.get("events") or {}).items()}
        if isinstance(config.get("events"), dict)
        else {},
        "webhook_configured": is_webhook_configured(config),
        "webhook_host": webhook_host(config.get(WEBHOOK_URL_KEY)),
    }
