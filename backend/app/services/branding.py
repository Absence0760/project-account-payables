"""Resolved brand context for branded outbound surfaces (PDFs + emails).

The white-label first slice stored per-tenant brand config on
``Organization.settings.brand`` (product name, logo URL, accent colors,
support / legal links) and themed the SPA. This module is the single place
that resolves that config into the fields the *outbound* surfaces need, with
platform defaults baked in — so the remittance / 1099 / audit PDFs and every
transactional email read brand from one helper instead of each re-deriving it.

Design rules (project invariants):

  * **Local-first.** Resolution never touches the network; ``get_brand_context``
    is pure. The optional logo *embed* helper (``fetch_logo_bytes``) is the only
    network touch and is fully best-effort: size- and time-bounded, and any
    failure falls back to product-name text — it never breaks PDF generation.
  * **No PII.** Brand chrome carries only the product name + logo + accent +
    support/legal links — never a bank number, tax id, or address.
  * **No hardcoded secrets.** Brand config is non-secret JSON; nothing here
    reads a secret.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from io import BytesIO

logger = logging.getLogger(__name__)

# Platform defaults — used for any brand field the tenant hasn't set. Kept in
# sync with the frontend brand store fallbacks (docs/white-label.md).
PLATFORM_PRODUCT_NAME = "Accounts Payable"
PLATFORM_ACCENT_COLOR = "#638cff"

# Same guards the BrandConfig schema enforces. We re-validate on read because a
# brand block could have been written before a validator existed, or hand-edited
# in the JSONB — a malformed value must degrade to the platform default, never
# flow into a PDF color or an <img src>.
_HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_URL_RE = re.compile(r"^https?://", re.IGNORECASE)

# Logo-embed safety bounds. A tenant-controlled URL must not be able to hang or
# exhaust memory in the PDF path, so we cap both the fetch time and the bytes.
LOGO_FETCH_TIMEOUT_SECONDS = 3.0
LOGO_MAX_BYTES = 1_048_576  # 1 MiB — generous for a header logo, cheap to embed.


@dataclass(frozen=True)
class BrandContext:
    """The resolved brand fields the outbound surfaces consume.

    Every field is non-empty *for the textual ones* (product_name, accent_color
    always carry a platform default); the URL fields are empty string when
    unset, since "" cleanly means "render nothing" for both <img> and <a>.
    """

    product_name: str
    logo_url: str
    accent_color: str
    support_url: str
    legal_url: str

    @property
    def has_logo(self) -> bool:
        return bool(self.logo_url)

    @property
    def has_support_url(self) -> bool:
        return bool(self.support_url)


def _clean_hex(value: object, default: str) -> str:
    if isinstance(value, str):
        v = value.strip()
        if v and _HEX_COLOR_RE.match(v):
            return v
    return default


def _clean_url(value: object) -> str:
    if isinstance(value, str):
        v = value.strip()
        if v and _URL_RE.match(v):
            return v
    return ""


def _clean_name(value: object, default: str) -> str:
    if isinstance(value, str):
        v = value.strip()
        if v:
            return v
    return default


def get_brand_context(org_settings: dict | None) -> BrandContext:
    """Resolve ``Organization.settings`` into a :class:`BrandContext`.

    Pure + total: tolerates ``None``, a missing ``brand`` block, a non-dict
    block, and individually malformed fields — each falls back to its platform
    default (text/accent) or to empty (URLs). Never raises, never hits the
    network.
    """
    settings = org_settings if isinstance(org_settings, dict) else {}
    brand = settings.get("brand")
    if not isinstance(brand, dict):
        brand = {}

    return BrandContext(
        product_name=_clean_name(brand.get("product_name"), PLATFORM_PRODUCT_NAME),
        logo_url=_clean_url(brand.get("logo_url")),
        accent_color=_clean_hex(brand.get("accent_color"), PLATFORM_ACCENT_COLOR),
        support_url=_clean_url(brand.get("support_url")),
        legal_url=_clean_url(brand.get("legal_url")),
    )


# ---------------------------------------------------------------------------
# PDF logo embed (best-effort, bounded, fail-soft)
# ---------------------------------------------------------------------------


def fetch_logo_bytes(logo_url: str | None) -> bytes | None:
    """Fetch a brand logo for embedding, bounded in time + size.

    Best-effort: returns the image bytes on success, or ``None`` on ANY problem
    (no URL, bad scheme, timeout, oversized, network error, non-2xx). The PDF
    renderers fall back to the product-name text when this returns ``None``, so
    logo embedding can never break PDF generation. Local-first: a tenant that
    sets no logo (or whose CDN is unreachable in a dev box) renders fine.
    """
    url = _clean_url(logo_url)
    if not url:
        return None

    # SSRF guard: refuse to fetch a logo whose host resolves to an internal
    # address (e.g. the cloud metadata endpoint). Any user can trigger this via
    # a PDF export, and the admin-set logo_url is otherwise unvalidated.
    from app.utils.url_safety import is_public_url

    if not is_public_url(url):
        logger.info("brand logo fetch refused: non-public URL; using product-name header")
        return None

    try:
        import httpx

        # follow_redirects is OFF: a redirect to an internal host would bypass
        # the pre-flight host check above. A redirecting CDN just yields a 3xx
        # here, which we treat as a miss and fall back to the text header.
        with httpx.Client(timeout=LOGO_FETCH_TIMEOUT_SECONDS, follow_redirects=False) as client:
            with client.stream("GET", url) as resp:
                if resp.status_code != 200:
                    return None
                # Reject anything obviously too large up front via the header,
                # then hard-cap the streamed bytes regardless (a lying or absent
                # Content-Length can't get past the byte budget).
                declared = resp.headers.get("content-length")
                if declared is not None:
                    try:
                        if int(declared) > LOGO_MAX_BYTES:
                            return None
                    except ValueError:
                        pass
                buf = bytearray()
                for chunk in resp.iter_bytes():
                    buf.extend(chunk)
                    if len(buf) > LOGO_MAX_BYTES:
                        return None
                return bytes(buf)
    except Exception:  # noqa: BLE001 — logo embed is best-effort; never propagate.
        # PII rule: nothing tenant-identifying here; the URL is non-secret brand
        # config, but we keep the log terse and value-free anyway.
        logger.info("brand logo fetch failed; falling back to product-name header")
        return None


def build_logo_flowable(brand: BrandContext, *, max_width_pt: float, max_height_pt: float):
    """Return a ReportLab ``Image`` flowable for the brand logo, or ``None``.

    Bounded by ``max_width_pt`` / ``max_height_pt`` and preserves aspect ratio.
    Returns ``None`` on any failure (no logo, fetch failed, undecodable image) so
    the caller renders the product-name text header instead. Imported lazily so
    this module stays importable without reportlab/PIL.
    """
    raw = fetch_logo_bytes(brand.logo_url)
    if raw is None:
        return None

    try:
        from PIL import Image as PILImage
        from reportlab.platypus import Image as RLImage

        with PILImage.open(BytesIO(raw)) as im:
            iw, ih = im.size
        if iw <= 0 or ih <= 0:
            return None

        scale = min(max_width_pt / iw, max_height_pt / ih, 1.0)
        # Always cap to the bounding box even when the source is small enough,
        # so a tiny source still fits and a huge one is clamped.
        draw_w = min(iw * scale, max_width_pt)
        draw_h = min(ih * scale, max_height_pt)
        return RLImage(BytesIO(raw), width=draw_w, height=draw_h)
    except Exception:  # noqa: BLE001 — undecodable / unsupported image → text fallback.
        logger.info("brand logo decode failed; falling back to product-name header")
        return None


# ---------------------------------------------------------------------------
# Email branding
# ---------------------------------------------------------------------------


def brand_email_from(brand: BrandContext, base_from_address: str) -> str:
    """Compose an RFC 5322 ``From`` value with the tenant product name.

    Keeps the configured sending address (the deliverable identity) and only
    swaps the display name to the tenant's product name, e.g.
    ``Acme Pay <no-reply@platform.com>``. Falls back to the bare address if the
    product name is empty (it never is — it carries a platform default) or the
    address already includes a display name.
    """
    name = (brand.product_name or "").strip()
    addr = (base_from_address or "").strip()
    if not addr or "<" in addr:
        # Already a full "Name <addr>" or empty — leave as-is.
        return addr or base_from_address
    if not name:
        return addr
    # Sanitize the display name for the header: no quotes / control chars that
    # could break the From line. Product name is length-capped + stripped by the
    # schema, but be defensive on read.
    safe = name.replace('"', "").replace("\r", "").replace("\n", "").strip()
    return f"{safe} <{addr}>"


def brand_email_html_header(brand: BrandContext) -> str:
    """A small branded HTML header line for transactional emails.

    Product name in the tenant accent color; PII-free. Callers prepend this to
    the body HTML. Returns plain markup (no <html>/<body> wrapper) so it
    composes with the existing per-template HTML fragments.
    """
    name = _escape_html(brand.product_name)
    accent = brand.accent_color  # already a validated hex literal or platform default
    return (
        f'<div style="border-bottom:2px solid {accent};padding-bottom:8px;'
        f'margin-bottom:16px;font-weight:600;font-size:16px;color:{accent}">'
        f"{name}</div>"
    )


def brand_email_footer_text(brand: BrandContext) -> str:
    """A plaintext footer line referencing the tenant support link, or ''.

    Empty when no support URL is configured (no platform default for support —
    the platform doesn't publish one per-tenant).
    """
    if not brand.has_support_url:
        return ""
    return f"Need help? {brand.support_url}"


def brand_email_footer_html(brand: BrandContext) -> str:
    """HTML companion to :func:`brand_email_footer_text`, or ''."""
    if not brand.has_support_url:
        return ""
    url = _escape_html(brand.support_url)
    return (
        f'<p style="color:#94a3b8;font-size:12px;margin-top:24px">'
        f'Need help? <a href="{url}">{url}</a></p>'
    )


def _escape_html(s: str) -> str:
    return (
        (s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
