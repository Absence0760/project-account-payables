"""`storage.get_file` optional key-prefix guardrail.

`get_file` is a raw S3 fetch with no tenant scoping of its own — every caller
must validate the key first. The opt-in `expected_prefix` parameter lets a
caller have the function enforce the leading `<org_id>/...` namespace itself,
raising an opaque 404 (never 403) on a mismatch BEFORE any bytes leave storage.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.services import storage


def test_get_file_rejects_key_outside_expected_prefix_without_touching_s3():
    org_a = str(uuid.uuid4())
    org_b = str(uuid.uuid4())
    foreign_key = f"{org_b}/chat/{uuid.uuid4()}/secret.pdf"

    with patch.object(storage, "_get_client") as get_client:
        with pytest.raises(HTTPException) as exc:
            storage.get_file(foreign_key, expected_prefix=f"{org_a}/")
    assert exc.value.status_code == 404
    # The guard must short-circuit before any storage client is built/fetched.
    get_client.assert_not_called()


def test_get_file_serves_key_inside_expected_prefix():
    org_a = str(uuid.uuid4())
    own_key = f"{org_a}/invoices/{uuid.uuid4()}/mine.pdf"

    body = MagicMock()
    body.read = MagicMock(return_value=b"MINE")
    fake_response = {"Body": body, "ContentType": "application/pdf"}
    fake_client = MagicMock()
    fake_client.get_object = MagicMock(return_value=fake_response)

    with patch.object(storage, "_get_client", return_value=fake_client):
        content, content_type = storage.get_file(own_key, expected_prefix=f"{org_a}/")
    assert content == b"MINE"
    assert content_type == "application/pdf"
