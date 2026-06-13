"""Content-type gate + size cap on the invoice upload path.

XML e-invoices (UBL / standalone CII) must pass the gate; oversized files —
including XML — must still reject at the 25 MB cap.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.services import storage
from app.services.storage import MAX_FILE_SIZE, upload_invoice_file


class _FakeUpload:
    def __init__(self, content: bytes, content_type: str, filename: str = "invoice.xml"):
        self._content = content
        self.content_type = content_type
        self.filename = filename

    async def read(self) -> bytes:
        return self._content


@pytest.mark.asyncio
async def test_xml_content_type_accepted():
    up = _FakeUpload(b"<Invoice/>", "application/xml", "invoice.xml")
    client = MagicMock()
    with (
        patch.object(storage, "_get_client", MagicMock(return_value=client)),
        patch.object(storage, "_ensure_bucket", MagicMock()),
    ):
        key, url = await upload_invoice_file(uuid.uuid4(), uuid.uuid4(), up)
    assert key.endswith("invoice.xml")
    client.put_object.assert_called_once()
    # The stored object preserves the XML content type.
    assert client.put_object.call_args.kwargs["ContentType"] == "application/xml"


@pytest.mark.asyncio
async def test_text_xml_content_type_accepted():
    up = _FakeUpload(b"<Invoice/>", "text/xml", "invoice.xml")
    client = MagicMock()
    with (
        patch.object(storage, "_get_client", MagicMock(return_value=client)),
        patch.object(storage, "_ensure_bucket", MagicMock()),
    ):
        await upload_invoice_file(uuid.uuid4(), uuid.uuid4(), up)
    client.put_object.assert_called_once()


@pytest.mark.asyncio
async def test_disallowed_content_type_rejected():
    up = _FakeUpload(b"x", "application/msword", "memo.doc")
    with pytest.raises(ValueError, match="not allowed"):
        await upload_invoice_file(uuid.uuid4(), uuid.uuid4(), up)


@pytest.mark.asyncio
async def test_oversized_xml_rejected_at_cap():
    up = _FakeUpload(b"<x/>" + b"a" * (MAX_FILE_SIZE + 1), "application/xml", "huge.xml")
    with pytest.raises(ValueError, match="maximum size"):
        await upload_invoice_file(uuid.uuid4(), uuid.uuid4(), up)
