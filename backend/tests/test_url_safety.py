"""SSRF guard (app/utils/url_safety) + the logo-fetch refusal it backs."""

from __future__ import annotations

import asyncio
import socket
import threading
from unittest.mock import patch

import pytest

from app.utils.url_safety import (
    UnsafeUrlError,
    assert_public_url,
    assert_public_url_async,
    is_public_url,
    is_public_url_async,
)

# Literal-IP / scheme cases need no DNS — deterministic offline.
UNSAFE = [
    "http://169.254.169.254/latest/meta-data/",  # cloud metadata link-local
    "http://127.0.0.1/",  # loopback
    "http://10.0.0.5/",  # RFC1918
    "http://192.168.1.1/",  # RFC1918
    "http://172.16.0.1/",  # RFC1918
    "http://0.0.0.0/",  # unspecified
    "http://[::1]/",  # IPv6 loopback
    "http://[fe80::1]/",  # IPv6 link-local
    "http://[::ffff:169.254.169.254]/",  # IPv4-mapped metadata
    "ftp://example.com/",  # non-http scheme
    "file:///etc/passwd",  # file scheme
    "https:///nohost",  # no host
]


@pytest.mark.parametrize("url", UNSAFE)
def test_assert_rejects_unsafe(url):
    with pytest.raises(UnsafeUrlError):
        assert_public_url(url)
    assert is_public_url(url) is False


def test_assert_allows_public_ip_literal():
    # A public IP literal needs no DNS and must pass.
    assert_public_url("https://8.8.8.8/")
    assert is_public_url("https://1.1.1.1/health") is True


def test_localhost_hostname_resolves_to_loopback_and_is_rejected():
    # `localhost` resolves to 127.0.0.1 / ::1 — a real, deterministic lookup.
    assert is_public_url("http://localhost:8000/") is False


def test_is_public_url_false_for_empty():
    assert is_public_url(None) is False
    assert is_public_url("") is False


def test_unresolvable_host_is_left_to_fail_naturally():
    # A host that doesn't resolve is NOT rejected here (the connection fails at
    # request time); rejecting on a transient DNS miss would be its own bug.
    assert_public_url("https://this-host-does-not-exist.invalid/")


def test_mixed_resolution_rejected_if_any_address_internal():
    # If a hostname resolves to BOTH a public and an internal address, reject —
    # defeats a record that mixes a decoy public IP with an internal one.
    fake = [
        (None, None, None, None, ("8.8.8.8", 443)),
        (None, None, None, None, ("169.254.169.254", 443)),
    ]
    with patch("app.utils.url_safety.socket.getaddrinfo", return_value=fake):
        with pytest.raises(UnsafeUrlError):
            assert_public_url("https://sneaky.example.com/")


# ---------------------------------------------------------------------------
# The logo fetch refuses an internal URL before making any request.
# ---------------------------------------------------------------------------


def test_fetch_logo_bytes_refuses_internal_url_without_fetching():
    from app.services import branding

    # Patch httpx.Client to explode if constructed — proves the guard returns
    # before any outbound request when the logo URL points at an internal host.
    with patch("httpx.Client", side_effect=AssertionError("SSRF: must not fetch internal URL")):
        assert branding.fetch_logo_bytes("http://169.254.169.254/logo.png") is None
        assert branding.fetch_logo_bytes("http://127.0.0.1:9200/logo.png") is None


# ---------------------------------------------------------------------------
# The awaitable twin: same verdicts, and it must NEVER call blocking
# `socket.getaddrinfo` — that stalls every other request on the worker.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("url", UNSAFE)
@pytest.mark.asyncio
async def test_async_rejects_the_same_unsafe_urls(url):
    with pytest.raises(UnsafeUrlError):
        await assert_public_url_async(url)
    assert await is_public_url_async(url) is False


@pytest.mark.asyncio
async def test_async_allows_public_ip_literal():
    await assert_public_url_async("https://8.8.8.8/")
    assert await is_public_url_async("https://1.1.1.1/health") is True


@pytest.mark.asyncio
async def test_async_rejects_localhost_hostname():
    assert await is_public_url_async("http://localhost:8000/") is False


@pytest.mark.asyncio
async def test_async_is_public_url_false_for_empty():
    assert await is_public_url_async(None) is False
    assert await is_public_url_async("") is False


@pytest.mark.asyncio
async def test_async_unresolvable_host_is_left_to_fail_naturally():
    await assert_public_url_async("https://this-host-does-not-exist.invalid/")


@pytest.mark.asyncio
async def test_async_mixed_resolution_rejected_if_any_address_internal():
    fake = [
        (None, None, None, None, ("8.8.8.8", 443)),
        (None, None, None, None, ("169.254.169.254", 443)),
    ]

    async def _fake_getaddrinfo(*args, **kwargs):
        return fake

    loop = asyncio.get_running_loop()
    with patch.object(loop, "getaddrinfo", _fake_getaddrinfo):
        with pytest.raises(UnsafeUrlError):
            await assert_public_url_async("https://sneaky.example.com/")


@pytest.mark.asyncio
async def test_async_guard_resolves_off_the_event_loop_thread():
    """The whole point of the async twin: DNS never runs ON the event loop.

    `loop.getaddrinfo` still ends in the stdlib `socket.getaddrinfo`, but in a
    worker thread. Recording the thread the lookup runs on is the exact,
    timing-free statement of the property: the sync `assert_public_url` resolves
    on the loop thread (and stalls every other request for the length of the
    lookup); the async twin must not.
    """
    loop_thread = threading.current_thread().ident
    seen: list[int | None] = []
    real = socket.getaddrinfo

    def _recording(*args, **kwargs):
        seen.append(threading.current_thread().ident)
        return real("127.0.0.1", 443, proto=socket.IPPROTO_TCP)

    with patch("app.utils.url_safety.socket.getaddrinfo", _recording):
        # Sanity: the SYNC form resolves on the caller's (loop) thread.
        assert is_public_url("https://example.com/") is False
        assert seen == [loop_thread]

        seen.clear()
        # The ASYNC form reaches the same verdict from a worker thread.
        assert await is_public_url_async("https://example.com/") is False
        assert seen and all(tid != loop_thread for tid in seen)


@pytest.mark.asyncio
async def test_chat_adapters_resolve_webhook_host_without_blocking():
    """Slack/Teams `send()` run the SSRF guard on the loop — it must not block.

    Both adapters post on every approval event. A blocking `getaddrinfo` here
    stalls every concurrent request for the length of the DNS lookup.
    """
    from app.services.chat_notification_adapters.base import ChatMessage
    from app.services.chat_notification_adapters.slack_adapter import (
        SlackChatNotificationAdapter,
    )
    from app.services.chat_notification_adapters.teams_adapter import (
        TeamsChatNotificationAdapter,
    )

    message = ChatMessage(
        event_type="invoice_approved",
        title="Invoice INV-1 was approved",
        invoice_number="INV-1",
        vendor_name="Acme",
        status="approved",
    )
    loop_thread = threading.current_thread().ident
    seen: list[int | None] = []
    real = socket.getaddrinfo

    def _recording(*args, **kwargs):
        seen.append(threading.current_thread().ident)
        return real("127.0.0.1", 443, proto=socket.IPPROTO_TCP)

    # A hostname (not a literal IP) so the guard has to resolve; it resolves to
    # loopback, so both adapters refuse before any HTTP is attempted.
    cfg = {"webhook_url": "http://hooks.internal.example/hook"}
    with patch("app.utils.url_safety.socket.getaddrinfo", _recording):
        with patch("httpx.AsyncClient", side_effect=AssertionError("must not post internally")):
            await SlackChatNotificationAdapter(cfg).send(message)
            await TeamsChatNotificationAdapter(cfg).send(message)

    assert len(seen) == 2, "both adapters must run the SSRF guard"
    assert all(tid != loop_thread for tid in seen), "DNS ran on the event loop thread"
