"""Pure unit tests for `services/chat_notifications_config`.

The module owns the shape rules for `Organization.settings.chat_notifications`,
whose `webhook_url` is the **credential** for both real chat providers. The
three properties asserted here are the ones a leak depends on:

  * `safe_status` can never emit the URL (only a boolean + the bare hostname);
  * `webhook_host` returns hostname only — never the path/query, which is where
    every provider puts its token;
  * `apply_config` preserves the credential, so saving the provider/event
    toggles can't silently drop it.

No DB, no network, no FastAPI.
"""

from __future__ import annotations

import pytest

from app.services.chat_notifications_config import (
    ChatConfigError,
    apply_config,
    apply_webhook_url,
    coerce_chat_config,
    is_webhook_configured,
    normalize_events,
    normalize_provider,
    safe_status,
    webhook_host,
)

# A realistically-shaped Slack incoming webhook. The token is the LAST path
# segment — the part that must never surface anywhere.
SLACK_URL = "https://hooks.slack.com/services/T0AAAAAAA/B0BBBBBBB/zzTOPSECRETzz"
TEAMS_URL = (
    "https://contoso.webhook.office.com/webhookb2/"
    "11111111-2222-3333-4444-555555555555@66666666-7777-8888-9999-000000000000/"
    "IncomingWebhook/deadbeefdeadbeefdeadbeefdeadbeef/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
)


# ---------- coerce_chat_config ----------------------------------------------


@pytest.mark.parametrize("raw", [None, "not-a-dict", 42, [], object()])
def test_coerce_tolerates_malformed_block(raw):
    """A persisted-but-invalid block must never break a read or a send."""
    assert coerce_chat_config(raw) == {}


def test_coerce_copies_rather_than_aliasing():
    src = {"enabled": True}
    out = coerce_chat_config(src)
    out["enabled"] = False
    assert src["enabled"] is True


# ---------- webhook_host: hostname ONLY --------------------------------------


def test_webhook_host_slack_drops_the_token_path():
    assert webhook_host(SLACK_URL) == "hooks.slack.com"


def test_webhook_host_teams_drops_the_guid_path():
    assert webhook_host(TEAMS_URL) == "contoso.webhook.office.com"


def test_webhook_host_drops_query_and_fragment():
    """Some providers put the token in the query string (Google Chat does)."""
    host = webhook_host("https://chat.example.test/v1/spaces/X?key=SECRET&token=ALSOSECRET#frag")
    assert host == "chat.example.test"


@pytest.mark.parametrize("raw", [None, "", "   ", 12345, {"url": SLACK_URL}, []])
def test_webhook_host_none_on_junk(raw):
    assert webhook_host(raw) is None


def test_webhook_host_never_raises_on_malformed_netloc():
    """An unterminated IPv6 literal makes `urlsplit` raise; the caller is a GET
    handler, so a corrupt settings row must degrade to None, not 500."""
    assert webhook_host("https://[::1/path") is None


def test_webhook_host_still_answers_when_only_the_port_is_junk():
    """`urlsplit(...).hostname` tolerates a non-numeric port (only `.port`
    raises), so this yields the host rather than None — asserted so a future
    "hardening" edit doesn't quietly start returning None for a live config."""
    assert webhook_host("https://hooks.slack.com:notaport/services/T/B/tok") == "hooks.slack.com"


# ---------- is_webhook_configured -------------------------------------------


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        ({}, False),
        ({"webhook_url": ""}, False),
        ({"webhook_url": "   "}, False),
        ({"webhook_url": None}, False),
        ({"webhook_url": 7}, False),
        ({"webhook_url": SLACK_URL}, True),
    ],
)
def test_is_webhook_configured(config, expected):
    assert is_webhook_configured(config) is expected


# ---------- normalize_provider / normalize_events ----------------------------


def test_normalize_provider_accepts_a_registered_key():
    assert normalize_provider(" slack ", supported=["mock", "slack", "teams"]) == "slack"


@pytest.mark.parametrize("bad", ["", "   ", None, 5, "sl4ck"])
def test_normalize_provider_refuses_unknown(bad):
    with pytest.raises(ChatConfigError):
        normalize_provider(bad, supported=["mock", "slack", "teams"])


def test_normalize_events_coerces_to_bools():
    out = normalize_events(
        {"invoice_approved": 1, "invoice_paid": 0},
        supported=("invoice_approved", "invoice_paid"),
    )
    assert out == {"invoice_approved": True, "invoice_paid": False}


def test_normalize_events_none_is_empty():
    assert normalize_events(None, supported=("invoice_paid",)) == {}


def test_normalize_events_refuses_unknown_key():
    """A typo'd event key would otherwise persist as a toggle that reads as
    configured in the UI and does nothing."""
    with pytest.raises(ChatConfigError) as exc:
        normalize_events({"invoice_payed": True}, supported=("invoice_paid",))
    assert "invoice_payed" in str(exc.value)


def test_normalize_events_refuses_non_mapping():
    with pytest.raises(ChatConfigError):
        normalize_events(["invoice_paid"], supported=("invoice_paid",))


# ---------- apply_config preserves the credential ----------------------------


def test_apply_config_preserves_webhook_url():
    """The regression this whole split exists to prevent: saving the provider /
    event toggles must not drop the credential."""
    before = {"enabled": False, "provider": "mock", "webhook_url": SLACK_URL}
    after = apply_config(before, enabled=True, provider="slack", events={"invoice_paid": False})
    assert after["webhook_url"] == SLACK_URL
    assert after["enabled"] is True
    assert after["provider"] == "slack"
    assert after["events"] == {"invoice_paid": False}


def test_apply_config_preserves_unknown_forward_compat_keys():
    before = {"webhook_url": SLACK_URL, "future_key": {"a": 1}}
    after = apply_config(before, enabled=True, provider="slack", events={})
    assert after["future_key"] == {"a": 1}


def test_apply_config_does_not_mutate_the_input():
    before = {"enabled": False, "webhook_url": SLACK_URL}
    apply_config(before, enabled=True, provider="slack", events={})
    assert before == {"enabled": False, "webhook_url": SLACK_URL}


# ---------- apply_webhook_url ------------------------------------------------


def test_apply_webhook_url_sets_and_strips():
    after = apply_webhook_url({"enabled": True}, f"  {SLACK_URL}  ")
    assert after["webhook_url"] == SLACK_URL
    assert after["enabled"] is True


def test_apply_webhook_url_replaces_atomically_with_no_overlap_slot():
    """A destination has nothing to overlap — unlike an HMAC signing secret,
    there is no counterpart key and no `previous_*` slot. Rotating must leave
    exactly ONE URL behind, or the retired (leaked) channel keeps receiving."""
    after = apply_webhook_url({"webhook_url": SLACK_URL}, TEAMS_URL)
    assert after["webhook_url"] == TEAMS_URL
    assert [k for k in after if "webhook" in k] == ["webhook_url"]


@pytest.mark.parametrize("empty", [None, "", "   "])
def test_apply_webhook_url_removes_the_key_entirely(empty):
    """Removal drops the key rather than storing "" so this module and the
    adapters' own fail-closed check agree on one representation of 'unset'."""
    after = apply_webhook_url({"webhook_url": SLACK_URL, "enabled": True}, empty)
    assert "webhook_url" not in after
    assert is_webhook_configured(after) is False
    assert after["enabled"] is True


def test_apply_webhook_url_remove_is_idempotent():
    once = apply_webhook_url({"enabled": True}, None)
    twice = apply_webhook_url(once, None)
    assert once == twice == {"enabled": True}


# ---------- safe_status: the credential cannot escape ------------------------


def test_safe_status_never_carries_the_url():
    config = {
        "enabled": True,
        "provider": "slack",
        "events": {"invoice_paid": False},
        "webhook_url": SLACK_URL,
    }
    status = safe_status(config)
    assert status == {
        "enabled": True,
        "provider": "slack",
        "events": {"invoice_paid": False},
        "webhook_configured": True,
        "webhook_host": "hooks.slack.com",
    }
    # Belt and braces: no fragment of the credential anywhere in the projection.
    assert "zzTOPSECRETzz" not in str(status)
    assert "/services/" not in str(status)


def test_safe_status_on_an_unconfigured_org():
    assert safe_status({}) == {
        "enabled": False,
        "provider": None,
        "events": {},
        "webhook_configured": False,
        "webhook_host": None,
    }


def test_safe_status_tolerates_malformed_sub_values():
    status = safe_status({"provider": 7, "events": "nope", "webhook_url": []})
    assert status["provider"] is None
    assert status["events"] == {}
    assert status["webhook_configured"] is False
    assert status["webhook_host"] is None
