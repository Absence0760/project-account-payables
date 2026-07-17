"""Unit tests for the outbound-webhook SSRF guard
(`app/services/webhooks/ssrf.py`).

Issue #171: a tenant admin controls a webhook's target_url and the delivery loop
POSTs signed payloads to it, so an internal / metadata / RFC1918 address must be
rejected. Pure — uses IP literals so no DNS is required.
"""

from __future__ import annotations

import pytest

from app.services.webhooks.ssrf import SsrfError, assert_public_webhook_url


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",  # AWS IMDS (link-local)
        "http://127.0.0.1/hook",  # loopback
        "http://127.0.0.1:8000/hook",  # loopback + port
        "http://10.0.0.5/hook",  # RFC1918
        "http://172.16.4.4/hook",  # RFC1918
        "http://192.168.1.10/hook",  # RFC1918
        "http://0.0.0.0/hook",  # unspecified
        "https://[::1]/hook",  # IPv6 loopback
        "https://[fc00::1]/hook",  # IPv6 unique-local
        "https://[fe80::1]/hook",  # IPv6 link-local
        "https://[::ffff:169.254.169.254]/hook",  # v4-mapped IMDS
    ],
)
def test_rejects_internal_addresses(url):
    with pytest.raises(SsrfError):
        assert_public_webhook_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://8.8.8.8/hook",  # public IPv4 literal
        "https://93.184.216.34/hook",  # public IPv4 literal
        "https://[2606:4700:4700::1111]/hook",  # public IPv6 literal (Cloudflare)
    ],
)
def test_allows_public_addresses(url):
    assert_public_webhook_url(url)  # no raise


def test_rejects_non_http_scheme():
    with pytest.raises(SsrfError):
        assert_public_webhook_url("ftp://8.8.8.8/hook")
    with pytest.raises(SsrfError):
        assert_public_webhook_url("file:///etc/passwd")


def test_rejects_missing_host():
    with pytest.raises(SsrfError):
        assert_public_webhook_url("http:///no-host")


def test_rejects_unresolvable_host():
    # A syntactically-valid but non-resolving host is rejected rather than
    # silently allowed (a real webhook target must resolve).
    with pytest.raises(SsrfError):
        assert_public_webhook_url("https://this-host-does-not-exist.invalid/hook")
