"""SSRF guard for server-side fetches of caller / admin-supplied URLs.

Several surfaces let an org admin store a URL that the backend then fetches
server-side: the white-label `logo_url` (embedded into generated PDFs — a fetch
ANY user can trigger via a PDF export), the Slack / Teams chat-notification
`webhook_url` (posted on every approval event), and the ERP / enrichment
adapter `base_url`. Without a guard, an admin could point any of these at an
internal address — most dangerously the cloud metadata endpoint
`169.254.169.254` — and read back instance credentials or probe the internal
network (SSRF).

`assert_public_url` resolves the host and rejects any address that isn't
publicly routable (private / loopback / link-local / reserved / multicast /
unspecified), in both IPv4 and IPv6. It rejects if ANY resolved address is
unsafe, which also defeats a DNS record that mixes a public and an internal IP.
A hostname that doesn't resolve is left alone — the connection fails naturally
at request time, and rejecting on a transient DNS miss would be its own bug.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class UnsafeUrlError(ValueError):
    """Raised when a URL is not an http(s) URL to a publicly routable host."""


def _is_unsafe_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    # `is_private` already covers RFC1918 + ULA (fc00::/7); the rest catch the
    # cloud metadata link-local (169.254.169.254 / fe80::), loopback, reserved,
    # multicast, and 0.0.0.0/::.
    if ip.is_private or ip.is_loopback or ip.is_link_local:
        return True
    if ip.is_reserved or ip.is_multicast or ip.is_unspecified:
        return True
    # IPv4-mapped IPv6 (e.g. ::ffff:169.254.169.254) — unwrap and re-check.
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None and _is_unsafe_ip(mapped):
        return True
    return False


def assert_public_url(url: str) -> None:
    """Raise ``UnsafeUrlError`` unless ``url`` is an http(s) URL whose host
    resolves only to publicly routable addresses."""
    parsed = urlparse(url or "")
    if parsed.scheme not in ("http", "https"):
        raise UnsafeUrlError("URL scheme must be http or https")
    host = parsed.hostname
    if not host:
        raise UnsafeUrlError("URL has no host")

    # A literal IP in the URL is checked directly (no DNS).
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if _is_unsafe_ip(literal):
            raise UnsafeUrlError("URL points at a non-public address")
        return

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        # Unresolvable — let the connection fail naturally rather than reject a
        # legitimate host that's momentarily unresolvable.
        return
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if _is_unsafe_ip(ip):
            raise UnsafeUrlError("URL resolves to a non-public address")


def is_public_url(url: str | None) -> bool:
    """Boolean form of :func:`assert_public_url` — ``False`` for None / empty /
    non-http(s) / internal-resolving URLs. Use at best-effort fetch sites that
    fail soft (return None / skip) rather than raising."""
    if not url:
        return False
    try:
        assert_public_url(url)
        return True
    except UnsafeUrlError:
        return False
