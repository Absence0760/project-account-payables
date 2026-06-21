"""SSRF guard (app/utils/url_safety) + the logo-fetch refusal it backs."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.utils.url_safety import UnsafeUrlError, assert_public_url, is_public_url

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
