"""Image pre-processing for extraction.

Currently: OSD-based auto-rotation. Rendered PDF pages from cameraphone
uploads, faxed docs, or misconfigured scanners frequently arrive at 90°,
180°, or 270° off-upright. Tesseract's OSD (Orientation and Script
Detection) detects these quarter-turn rotations cheaply and without AI.

Soft dependencies: ``pytesseract`` and ``Pillow``, plus the
``tesseract`` binary + the ``osd`` traineddata file on PATH. Any missing
piece makes auto-rotate a no-op — extraction still proceeds with the
original bytes, so a misconfigured worker host never blocks an invoice.

Small-angle deskew (1–5° tilt) is intentionally out of scope here — OSD
only handles quarter turns. Add a separate pass (OpenCV Hough or
``minAreaRect``) if real-world data shows enough tilted uploads to
warrant it.
"""

from __future__ import annotations

import io
import logging

logger = logging.getLogger(__name__)


def auto_rotate_png(img_bytes: bytes) -> bytes:
    """Run Tesseract OSD on a PNG and return upright bytes.

    Returns the input unchanged when OSD is unavailable or reports no
    rotation needed. Never raises — extraction must not fail because the
    preprocessing host is missing tesseract.
    """
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        return img_bytes

    try:
        img = Image.open(io.BytesIO(img_bytes))
        osd = pytesseract.image_to_osd(img, output_type=pytesseract.Output.DICT)
        rotate = int(osd.get("rotate", 0)) % 360
        if rotate == 0:
            return img_bytes
        # OSD "rotate" is the clockwise angle that brings the page upright.
        # PIL's Image.rotate is counter-clockwise for positive angles, so
        # negate to perform the equivalent clockwise rotation.
        rotated = img.rotate(-rotate, expand=True)
        out = io.BytesIO()
        rotated.save(out, format="PNG")
        return out.getvalue()
    except Exception as exc:  # noqa: BLE001 — OSD failure must not break extraction
        # Class only, never the message — the OSD path runs over uploaded invoice
        # page images, so keep any document content out of logs (PII-out-of-logs).
        logger.debug("Auto-rotate skipped: %s", exc.__class__.__name__)
        return img_bytes


def auto_rotate_pages(pages: list[bytes]) -> list[bytes]:
    """Apply ``auto_rotate_png`` to every page image."""
    return [auto_rotate_png(p) for p in pages]
