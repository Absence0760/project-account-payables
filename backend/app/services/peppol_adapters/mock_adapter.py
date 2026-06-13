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

import hashlib

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
        # Inbound-ready stub — the next slice implements verify + dedupe via
        # webhook_security. Returns None so a premature call is a no-op rather
        # than a crash.
        return None
