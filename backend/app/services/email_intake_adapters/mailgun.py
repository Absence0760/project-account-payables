"""Mailgun inbound route webhook → InboundEmail.

Mailgun posts ``multipart/form-data`` on inbound routes. Fields of
interest:

    - recipient : the ``To`` value that matched the route
    - sender    : envelope From
    - subject
    - Message-Id
    - attachment-count
    - attachment-N : file uploads

The webhook endpoint parses the multipart into a dict of fields +
attachments and passes the flat dict (serialized as JSON) to this
parser, since the adapters all take bytes for API consistency.
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

    to = data.get("recipient") or data.get("To") or ""
    sender = data.get("sender") or data.get("From") or ""
    if not to or not sender:
        return None

    attachments = []
    count = int(data.get("attachment-count") or 0)
    for i in range(1, count + 1):
        att = data.get(f"attachment-{i}")
        if not isinstance(att, dict):
            continue
        raw_b64 = att.get("content_base64") or ""
        try:
            content = base64.b64decode(raw_b64) if raw_b64 else b""
        except Exception:  # noqa: BLE001
            continue
        attachments.append(
            InboundAttachment(
                filename=att.get("filename") or f"attachment-{i}",
                content_type=(att.get("content-type") or "").lower(),
                content=content,
            )
        )

    return InboundEmail(
        to=to,
        sender=sender,
        subject=data.get("subject") or "",
        message_id=data.get("Message-Id") or "",
        attachments=attachments,
    )
