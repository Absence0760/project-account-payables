"""Unit tests for email-to-invoice intake.

DB-free where possible; the full process_inbound_email path touches a
tenant engine so those cases are covered by the smoke stack. Here we
test: token generation + address rendering, token extraction, HMAC
verification, and the provider adapters' parsing.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Token + address helpers
# ---------------------------------------------------------------------------


def test_generate_intake_token_is_url_safe():
    from app.services.email_intake import generate_intake_token

    token = generate_intake_token(length=16)
    assert len(token) == 16
    # token_urlsafe alphabet: letters, digits, underscore, hyphen
    assert all(c.isalnum() or c in "_-" for c in token)


def test_provision_intake_token_sets_enabled_flag_and_token():
    from app.services.email_intake import provision_intake_token

    org = SimpleNamespace(settings={})
    token = provision_intake_token(org)

    assert org.settings["email_intake"]["token"] == token
    assert org.settings["email_intake"]["enabled"] is True
    assert "rotated_at" in org.settings["email_intake"]


def test_provision_intake_token_preserves_other_settings():
    from app.services.email_intake import provision_intake_token

    org = SimpleNamespace(settings={"company": {"name": "Acme"}})
    provision_intake_token(org)

    assert org.settings["company"] == {"name": "Acme"}


def test_intake_address_returns_none_when_domain_unset():
    from app.services import email_intake

    org = SimpleNamespace(settings={"email_intake": {"token": "abc", "enabled": True}})
    with patch.object(email_intake.settings, "email_intake_domain", ""):
        assert email_intake.intake_address_for(org) is None


def test_intake_address_returns_none_when_token_missing():
    from app.services import email_intake

    org = SimpleNamespace(settings={})
    with patch.object(email_intake.settings, "email_intake_domain", "ap.example.com"):
        assert email_intake.intake_address_for(org) is None


def test_intake_address_renders_correctly():
    from app.services import email_intake

    org = SimpleNamespace(settings={"email_intake": {"token": "abc123", "enabled": True}})
    with patch.object(email_intake.settings, "email_intake_domain", "ap.example.com"):
        assert email_intake.intake_address_for(org) == "invoices+abc123@ap.example.com"


# ---------------------------------------------------------------------------
# Token extraction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "to,expected",
    [
        ("invoices+abc123@ap.example.com", "abc123"),
        ("INVOICES+abc_DEF-123@ap.example.com", "abc_DEF-123"),
        ('"AP Intake" <invoices+xyz@ap.example.com>', "xyz"),
        ("invoices@ap.example.com", None),
        ("", None),
        (None, None),
    ],
)
def test_extract_token(to, expected):
    from app.services.email_intake import extract_token

    assert extract_token(to) == expected


# ---------------------------------------------------------------------------
# HMAC verification
# ---------------------------------------------------------------------------


def test_verify_signature_skips_when_secret_unset():
    from app.services import email_intake

    with patch.object(email_intake.settings, "email_intake_signing_secret", ""):
        assert email_intake.verify_signature(b"anything", None) is True
        assert email_intake.verify_signature(b"anything", "wrong") is True


def test_verify_signature_rejects_missing_header_with_secret():
    from app.services import email_intake

    with patch.object(email_intake.settings, "email_intake_signing_secret", "super-secret"):
        assert email_intake.verify_signature(b"body", None) is False


def test_verify_signature_accepts_valid_signature():
    from app.services import email_intake

    body = b"payload"
    secret = "s3cr3t"
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    with patch.object(email_intake.settings, "email_intake_signing_secret", secret):
        assert email_intake.verify_signature(body, sig) is True
        assert email_intake.verify_signature(body, "deadbeef") is False


# ---------------------------------------------------------------------------
# Provider adapters
# ---------------------------------------------------------------------------


def test_generic_adapter_parses_normalized_json():
    from app.services.email_intake_adapters import get_parser

    pdf = b"%PDF-1.4 fake"
    body = json.dumps(
        {
            "to": "invoices+tok@ap.example.com",
            "from": "ap@vendor.com",
            "subject": "Invoice 42",
            "attachments": [
                {
                    "filename": "inv.pdf",
                    "content_type": "application/pdf",
                    "content_base64": base64.b64encode(pdf).decode(),
                }
            ],
        }
    ).encode()

    parser = get_parser("generic")
    parsed = parser(body, {})

    assert parsed is not None
    assert parsed.to == "invoices+tok@ap.example.com"
    assert parsed.sender == "ap@vendor.com"
    assert parsed.subject == "Invoice 42"
    assert len(parsed.attachments) == 1
    assert parsed.attachments[0].content == pdf
    assert parsed.attachments[0].content_type == "application/pdf"


def test_generic_adapter_returns_none_on_malformed_json():
    from app.services.email_intake_adapters import get_parser

    parser = get_parser("generic")
    assert parser(b"not-json", {}) is None


def test_generic_adapter_rejects_missing_to_or_from():
    from app.services.email_intake_adapters import get_parser

    parser = get_parser("generic")
    assert parser(b'{"to": "", "from": "x@y.com"}', {}) is None
    assert parser(b'{"to": "x@y.com", "from": ""}', {}) is None


def test_ses_adapter_parses_mime():
    """SES posts a full MIME message inside SNS's envelope; the adapter
    unwraps both layers and surfaces the first PDF attachment."""
    from app.services.email_intake_adapters import get_parser

    pdf = b"%PDF-1.4 fake"
    pdf_b64 = base64.b64encode(pdf).decode()
    raw_mime = (
        "From: ap@vendor.com\r\n"
        "To: invoices+tok@ap.example.com\r\n"
        "Subject: Monthly invoice\r\n"
        "MIME-Version: 1.0\r\n"
        'Content-Type: multipart/mixed; boundary="boundary"\r\n'
        "\r\n"
        "--boundary\r\n"
        "Content-Type: text/plain\r\n\r\n"
        "See attached.\r\n"
        "--boundary\r\n"
        "Content-Type: application/pdf\r\n"
        "Content-Transfer-Encoding: base64\r\n"
        'Content-Disposition: attachment; filename="invoice.pdf"\r\n\r\n'
        f"{pdf_b64}\r\n"
        "--boundary--\r\n"
    )
    envelope = {"Message": json.dumps({"content": raw_mime})}
    body = json.dumps(envelope).encode()

    parser = get_parser("ses")
    parsed = parser(body, {})

    assert parsed is not None
    assert parsed.sender == "ap@vendor.com"
    assert "invoices+tok@ap.example.com" in parsed.to
    pdfs = [a for a in parsed.attachments if a.content_type == "application/pdf"]
    assert len(pdfs) == 1
    assert pdfs[0].filename == "invoice.pdf"
    assert pdfs[0].content == pdf


def test_mailgun_adapter_parses_json_form():
    from app.services.email_intake_adapters import get_parser

    pdf = b"%PDF fake"
    body = json.dumps(
        {
            "recipient": "invoices+tok@ap.example.com",
            "sender": "ap@vendor.com",
            "subject": "Inv",
            "attachment-count": 1,
            "attachment-1": {
                "filename": "inv.pdf",
                "content-type": "application/pdf",
                "content_base64": base64.b64encode(pdf).decode(),
            },
        }
    ).encode()

    parser = get_parser("mailgun")
    parsed = parser(body, {})

    assert parsed is not None
    assert parsed.to == "invoices+tok@ap.example.com"
    assert len(parsed.attachments) == 1
    assert parsed.attachments[0].content == pdf


def test_unknown_provider_returns_none():
    from app.services.email_intake_adapters import get_parser

    assert get_parser("totally-made-up") is None


# ---------------------------------------------------------------------------
# Attachment filtering
# ---------------------------------------------------------------------------


def test_usable_attachments_drops_wrong_content_type():
    from app.services.email_intake import (
        InboundAttachment,
        IntakeResult,
        _usable_attachments,
    )

    atts = [
        InboundAttachment(filename="a.pdf", content_type="application/pdf", content=b"x"),
        InboundAttachment(filename="b.exe", content_type="application/octet-stream", content=b"x"),
        InboundAttachment(filename="c.jpg", content_type="image/jpeg", content=b"x"),
        InboundAttachment(filename="d.pdf", content_type="application/pdf", content=b""),
    ]
    result = IntakeResult()

    kept = list(_usable_attachments(atts, result))

    assert len(kept) == 2
    assert {a.filename for a in kept} == {"a.pdf", "c.jpg"}
    assert any("b.exe" in s for s in result.skipped_attachments)
    assert any("d.pdf" in s for s in result.skipped_attachments)
