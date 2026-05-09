"""Unit tests for image_preprocess.auto_rotate_png.

Tesseract is a runtime-optional dependency — these tests stub pytesseract
via ``sys.modules`` so they run in CI hosts that don't have the binary
installed. The goal is to prove:

1. When OSD reports no rotation, the bytes are returned unchanged.
2. When OSD reports 90/180/270, the image is rotated and new bytes come back.
3. When pytesseract raises (no tesseract binary on PATH), the original
   bytes are returned — extraction must not fail because preprocessing does.
"""

from __future__ import annotations

import io
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock


def _make_png(size: tuple[int, int] = (200, 100), color: str = "white") -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, color=color).save(buf, format="PNG")
    return buf.getvalue()


def _install_fake_pytesseract(monkeypatch, *, rotate: int = 0, raises: bool = False) -> MagicMock:
    fake = SimpleNamespace()
    fake.Output = SimpleNamespace(DICT="dict")

    def image_to_osd(img, output_type):  # noqa: ARG001
        if raises:
            raise RuntimeError("tesseract not found")
        return {"rotate": rotate}

    fake.image_to_osd = MagicMock(side_effect=image_to_osd)
    monkeypatch.setitem(sys.modules, "pytesseract", fake)
    return fake.image_to_osd


def test_auto_rotate_returns_input_when_osd_reports_zero(monkeypatch):
    from app.services.image_preprocess import auto_rotate_png

    spy = _install_fake_pytesseract(monkeypatch, rotate=0)
    original = _make_png()

    result = auto_rotate_png(original)

    assert result == original
    spy.assert_called_once()


def test_auto_rotate_applies_90_degree_rotation(monkeypatch):
    from PIL import Image

    from app.services.image_preprocess import auto_rotate_png

    _install_fake_pytesseract(monkeypatch, rotate=90)
    original = _make_png(size=(200, 100))  # landscape

    result = auto_rotate_png(original)

    assert result != original
    rotated_img = Image.open(io.BytesIO(result))
    # OSD "rotate=90" is clockwise to upright; PIL rotate(-90, expand=True)
    # swaps width/height on a landscape source → portrait.
    assert rotated_img.size == (100, 200)


def test_auto_rotate_no_op_when_pytesseract_missing(monkeypatch):
    from app.services.image_preprocess import auto_rotate_png

    # Hide pytesseract — simulates a host without the dep installed.
    monkeypatch.setitem(sys.modules, "pytesseract", None)
    original = _make_png()

    result = auto_rotate_png(original)

    assert result == original


def test_auto_rotate_no_op_when_osd_raises(monkeypatch):
    """If the tesseract binary isn't on PATH, pytesseract raises — we swallow it."""
    from app.services.image_preprocess import auto_rotate_png

    _install_fake_pytesseract(monkeypatch, raises=True)
    original = _make_png()

    result = auto_rotate_png(original)

    assert result == original


def test_auto_rotate_pages_applies_to_each_image(monkeypatch):
    from app.services.image_preprocess import auto_rotate_pages

    spy = _install_fake_pytesseract(monkeypatch, rotate=0)
    pages = [_make_png(), _make_png(), _make_png()]

    result = auto_rotate_pages(pages)

    assert len(result) == 3
    assert spy.call_count == 3
