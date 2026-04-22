"""Post-deploy TLS smoke test.

Usable as a CI step after a deploy: given an origin URL (e.g.
`https://api.example.com`), it asserts:

1. The TLS handshake succeeds against a real (not self-signed) cert.
2. The origin returns `Strict-Transport-Security` on a plain `GET /`.
3. Plain-HTTP on port 80 redirects to HTTPS.

Exits non-zero on any failure so CI can pick it up. Requires only stdlib
plus `httpx` (already a backend dep).

Usage:

    python scripts/verify_tls.py https://api.example.com
    python scripts/verify_tls.py https://api.example.com --path /api/health

Part of SOC 2 engineering prereqs in `docs/soc2-readiness.md`.
"""

from __future__ import annotations

import argparse
import socket
import ssl
import sys
from urllib.parse import urlparse

import httpx


class VerificationError(Exception):
    """Raised when a specific check fails. Message is user-facing."""


def _check_tls_handshake(host: str, port: int) -> ssl.SSLContext:
    """Open a TLS socket with the system trust store and verify the peer.

    Returns the validated context on success; raises VerificationError with
    a short reason on failure.
    """
    ctx = ssl.create_default_context()
    # Default context verifies the chain and hostname. A self-signed cert
    # or mismatched SAN raises ssl.SSLCertVerificationError here.
    try:
        with socket.create_connection((host, port), timeout=10) as raw:
            with ctx.wrap_socket(raw, server_hostname=host) as tls:
                cert = tls.getpeercert()
                if not cert:
                    raise VerificationError("TLS handshake returned no peer cert")
    except ssl.SSLCertVerificationError as exc:
        raise VerificationError(f"cert verification failed: {exc.reason}") from exc
    except (OSError, ssl.SSLError) as exc:
        raise VerificationError(f"TLS handshake failed: {exc}") from exc
    return ctx


def _check_hsts(origin: str, path: str) -> str:
    url = origin.rstrip("/") + path
    try:
        response = httpx.get(url, timeout=10, follow_redirects=True)
    except httpx.HTTPError as exc:
        raise VerificationError(f"HTTPS request failed: {exc}") from exc
    hsts = response.headers.get("strict-transport-security")
    if not hsts:
        raise VerificationError("no Strict-Transport-Security header on the HTTPS response")
    return hsts


def _check_http_redirect(host: str) -> str:
    """Plain-HTTP request to port 80 must redirect to HTTPS."""
    url = f"http://{host}/"
    try:
        response = httpx.get(url, timeout=10, follow_redirects=False)
    except httpx.HTTPError as exc:
        raise VerificationError(f"HTTP-to-HTTPS probe failed: {exc}") from exc
    if response.status_code not in (301, 302, 307, 308):
        raise VerificationError(f"expected HTTP redirect status, got {response.status_code}")
    location = response.headers.get("location", "")
    if not location.lower().startswith("https://"):
        raise VerificationError(f"redirect target is not HTTPS (got {location!r})")
    return location


def verify(origin: str, path: str) -> list[str]:
    """Run all four checks. Returns a list of human-readable OK lines.

    Raises VerificationError on the first failing check.
    """
    parsed = urlparse(origin)
    if parsed.scheme != "https":
        raise VerificationError(f"origin must be https://, got {parsed.scheme!r}")
    if not parsed.hostname:
        raise VerificationError(f"origin is missing a hostname: {origin!r}")

    host = parsed.hostname
    port = parsed.port or 443
    results: list[str] = []

    _check_tls_handshake(host, port)
    results.append(f"OK  TLS handshake + cert verification @ {host}:{port}")

    hsts = _check_hsts(origin, path)
    results.append(f"OK  HSTS header present: {hsts}")

    location = _check_http_redirect(host)
    results.append(f"OK  HTTP -> HTTPS redirect ({location})")

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("origin", help="https://host[:port] to probe")
    parser.add_argument(
        "--path",
        default="/api/health",
        help="Path to fetch for the HSTS check (default: /api/health)",
    )
    args = parser.parse_args()

    try:
        for line in verify(args.origin, args.path):
            print(line)
    except VerificationError as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1
    print("All TLS checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
