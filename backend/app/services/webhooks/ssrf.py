"""SSRF guard for outbound-webhook target URLs.

A tenant admin controls a webhook's ``target_url``, and the delivery loop POSTs
signed invoice/payment payloads to it. Without a destination check that admin
could point the hook at an internal address — the cloud metadata endpoint
(``169.254.169.254``), an RFC1918 host, or ``localhost`` — and turn the server
into an SSRF proxy (issue #171).

``assert_public_webhook_url`` resolves the host and rejects any address that is
not globally routable (loopback / private / link-local / unique-local /
multicast / reserved / unspecified). It runs at **both** config time (create /
update) and again immediately before **each** dispatch, so a hostname whose DNS
later flips to an internal IP is caught at send time too.

Residual: a sub-second DNS-rebind between our resolution and httpx's own connect
is not fully closed here (that needs connection-level IP pinning or a locked-down
egress proxy — the durable control). Re-validating at dispatch narrows the
window to that race; the static-target exploit (a hook literally pointed at IMDS
/ RFC1918) is fully closed.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class SsrfError(ValueError):
    """A target URL is missing a host, unresolvable, or resolves to a
    non-public address. Subclasses ``ValueError`` so Pydantic/endpoint layers
    surface it as a 422/400, never a 500."""


def _ip_is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True only for a globally-routable unicast address.

    ``is_private`` already covers RFC1918 (10/8, 172.16/12, 192.168/16), IPv6
    unique-local (fc00::/7) and IPv4 link-local-as-private carve-outs; the rest
    are called out explicitly so metadata (``169.254.169.254`` → link-local) and
    loopback are unambiguously blocked.
    """
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _resolve(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Resolve a hostname to every A/AAAA address. Blocking — call via a thread
    in async contexts (``assert_public_webhook_url_async``)."""
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    out: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    seen: set[str] = set()
    for _family, _type, _proto, _canon, sockaddr in infos:
        addr = sockaddr[0]
        if addr not in seen:
            seen.add(addr)
            out.append(ipaddress.ip_address(addr))
    return out


def assert_public_webhook_url(url: str) -> None:
    """Raise ``SsrfError`` unless ``url`` is an http(s) URL whose host resolves
    exclusively to public addresses. Blocking (does DNS) — use the async wrapper
    from request/dispatch paths."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise SsrfError("target_url must be an http(s) URL")
    host = parsed.hostname
    if not host:
        raise SsrfError("target_url has no host")

    try:
        candidates: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = [
            ipaddress.ip_address(host)
        ]
    except ValueError:
        try:
            candidates = _resolve(host)
        except socket.gaierror as exc:
            raise SsrfError("target_url host does not resolve") from exc

    if not candidates:
        raise SsrfError("target_url host does not resolve")

    for ip in candidates:
        # Unwrap an IPv4-mapped IPv6 address (::ffff:169.254.169.254) so the
        # v4 range checks apply.
        if ip.version == 6 and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        if not _ip_is_public(ip):
            raise SsrfError("target_url resolves to a non-public address")


async def assert_public_webhook_url_async(url: str) -> None:
    """Async wrapper — runs the blocking DNS check off the event loop."""
    import asyncio

    await asyncio.to_thread(assert_public_webhook_url, url)
