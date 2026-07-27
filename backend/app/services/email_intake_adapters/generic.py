"""Pass-through parser for a normalized JSON payload.

Expected body:
    {
      "to": "invoices+abc123@ap.feohledger.com",
      "from": "ap@vendor.com",
      "subject": "Invoice INV-001",
      "message_id": "<opaque>",
      "attachments": [
        {"filename": "inv.pdf", "content_type": "application/pdf",
         "content_base64": "..."}
      ]
    }

Handy for: tests, hand-rolled forwarders (e.g. a Postmark → webhook
Lambda), and any provider not yet natively supported.
"""

from __future__ import annotations

import base64
import json

from app.services.email_intake import InboundAttachment, InboundEmail


def parse(body: bytes, headers: dict[str, str]) -> InboundEmail | None:
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None

    to = data.get("to") or ""
    sender = data.get("from") or ""
    if not to or not sender:
        return None

    attachments = []
    for att in data.get("attachments") or []:
        raw_b64 = att.get("content_base64") or ""
        try:
            content = base64.b64decode(raw_b64) if raw_b64 else b""
        except Exception:  # noqa: BLE001
            content = b""
        attachments.append(
            InboundAttachment(
                filename=att.get("filename") or "attachment",
                content_type=(att.get("content_type") or "").lower(),
                content=content,
            )
        )

    return InboundEmail(
        to=to,
        sender=sender,
        subject=data.get("subject") or "",
        message_id=data.get("message_id") or "",
        attachments=attachments,
    )
