"""Mock PEPPOL Access Point adapter — local dev + test default.

Simulates a hosted Access Point entirely in-process: no DNS, no SMP/SML
lookup, no network. This is the default provider (``AP_PEPPOL_PROVIDER=mock``)
so ``pnpm dev`` transmits e-invoices end-to-end without a real credential.

Resolution: returns a canned :class:`ParticipantCapability` for any receiver
whose scheme is a known EAS code (so the happy path works for arbitrary test
ids), and ``registered=False`` for a participant whose value is on the
``unknown``/sentinel list — exercising the ``receiver_not_registered`` failure
path. Send always succeeds with a deterministic-shape MessageId.
"""

from __future__ import annotations

import base64
import hashlib
import json

from app.services.peppol_adapters.base import (
    ParticipantCapability,
    ParticipantId,
    PeppolAdapter,
    TransmissionRequest,
    TransmissionResult,
)
from app.services.peppol_adapters.constants import PEPPOL_BIS_BILLING_DOCTYPE
from app.services.peppol_adapters.dispatcher import register_peppol_adapter

# Canned C3 endpoint the mock SMP returns for a registered receiver.
_MOCK_ACCESS_POINT_URL = "https://ap.mock-peppol.invalid/as4"

# A receiver value containing this sentinel resolves as NOT registered, so a
# test can deterministically drive the failure path without real SMP data.
_UNREGISTERED_SENTINEL = "UNREGISTERED"


@register_peppol_adapter("mock")
class MockPeppolAdapter(PeppolAdapter):
    provider_name = "mock"

    async def resolve_participant(self, participant_id: ParticipantId) -> ParticipantCapability:
        # No network: a value carrying the sentinel (or empty) is "not
        # registered"; everything else resolves to a canned capability.
        if not participant_id.value or _UNREGISTERED_SENTINEL in participant_id.value.upper():
            return ParticipantCapability(
                participant_id=participant_id,
                registered=False,
                unregistered_reason="receiver_not_registered",
            )
        return ParticipantCapability(
            participant_id=participant_id,
            registered=True,
            access_point_url=_MOCK_ACCESS_POINT_URL,
            supported_doc_types=(PEPPOL_BIS_BILLING_DOCTYPE,),
        )

    async def send(self, request: TransmissionRequest) -> TransmissionResult:
        # Deterministic MessageId derived from the business message id so a
        # retry of the same logical transmission yields a stable id — mirrors
        # how a real AP dedupes on our idempotency key. No PII in the id.
        digest = hashlib.sha256(request.business_message_id.encode()).hexdigest()[:24]
        message_id = f"mock-msg-{digest}"
        return TransmissionResult(
            success=True,
            message_id=message_id,
            status="sent",
            raw_response={
                "mock": True,
                "message_id": message_id,
                "business_message_id": request.business_message_id,
                "doc_type": request.doc_type_id,
            },
        )

    async def test_connection(self) -> bool:
        return True

    def parse_inbound(self, headers: dict, body: bytes):
        """Parse a dev-shaped inbound delivery into an InboundPeppolMessage.

        The HMAC over ``body`` is verified BEFORE this is called (in the route),
        so this only unpacks the (already-trusted) envelope. Two dev shapes are
        accepted so the local webhook is easy to exercise:

        1. **JSON envelope** — ``{"message_id", "sender_scheme", "sender_value",
           "doc_type_id", "process_id", "payload_base64"}``. ``payload_base64``
           (or a raw ``payload`` string) carries the UBL/CII bytes. This is the
           shape a real gateway's inbound delivery most resembles.
        2. **Raw UBL body + metadata headers** — the body IS the UBL/CII XML and
           the metadata rides on ``X-Peppol-Message-Id`` /
           ``X-Peppol-Sender-Scheme`` / ``X-Peppol-Sender-Value`` /
           ``X-Peppol-Doc-Type`` / ``X-Peppol-Process-Id``.

        Returns ``None`` when the message id can't be determined (so the route
        refuses a delivery it could never dedupe), mirroring the email-intake
        parse-None drop.
        """
        # Local import to avoid a module-level import cycle (peppol_receive
        # imports the adapter package).
        from app.services.peppol_receive import InboundPeppolMessage

        message_id = ""
        sender_scheme = ""
        sender_value = ""
        doc_type_id = PEPPOL_BIS_BILLING_DOCTYPE
        process_id = ""
        payload: bytes = b""

        envelope: dict | None = None
        text = (body or b"").lstrip()
        if text[:1] in (b"{", b"["):
            try:
                envelope = json.loads(body.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                envelope = None

        if isinstance(envelope, dict):
            message_id = str(envelope.get("message_id") or "")
            sender_scheme = str(envelope.get("sender_scheme") or "")
            sender_value = str(envelope.get("sender_value") or "")
            doc_type_id = str(envelope.get("doc_type_id") or PEPPOL_BIS_BILLING_DOCTYPE)
            process_id = str(envelope.get("process_id") or "")
            b64 = envelope.get("payload_base64")
            if b64:
                try:
                    payload = base64.b64decode(b64)
                except (ValueError, TypeError):
                    payload = b""
            elif envelope.get("payload"):
                payload = str(envelope["payload"]).encode("utf-8")
        else:
            # Raw UBL body + metadata headers (case-insensitive lookup).
            lower = {k.lower(): v for k, v in (headers or {}).items()}
            message_id = lower.get("x-peppol-message-id", "")
            sender_scheme = lower.get("x-peppol-sender-scheme", "")
            sender_value = lower.get("x-peppol-sender-value", "")
            doc_type_id = lower.get("x-peppol-doc-type", PEPPOL_BIS_BILLING_DOCTYPE)
            process_id = lower.get("x-peppol-process-id", "")
            payload = body or b""

        if not message_id:
            return None

        return InboundPeppolMessage(
            message_id=message_id,
            sender_scheme=sender_scheme,
            sender_value=sender_value,
            doc_type_id=doc_type_id,
            process_id=process_id,
            payload=payload,
        )
