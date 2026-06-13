"""AS4 gateway adapter — transmits via a hosted PEPPOL Access Point's HTTP API.

We are corner C1/C2-client: this adapter speaks the gateway's REST API, and
the gateway performs the AS4/ebMS3 handshake to the receiver's Access Point
(C3). We do NOT implement raw AS4 SOAP here.

The gateway base URL + API key come from config (per-org
``Organization.settings.peppol`` first, then the process-level
``settings.peppol_gateway_*``). The API key has NO hardcoded fallback — when
it is empty the adapter returns the PII-free ``peppol_not_configured`` outcome
(mirrors ``increase``'s ``increase_not_configured``) instead of attempting a
networked call. The live key is supplied via sops in deployed envs.

SBDH (Standard Business Document Header) wrapping happens HERE, in the adapter
layer — never in the e_invoice generator (which only emits clean UBL). Most
hosted APs accept the raw UBL plus the participant/doc-type/process metadata
and build the SBDH server-side; we pass that metadata in the request body and
let the gateway wrap. If a gateway requires a client-built SBDH, this is the
single place to add it.
"""

from __future__ import annotations

import base64
import logging

import httpx

from app.config import settings
from app.services.peppol_adapters.base import (
    ParticipantCapability,
    ParticipantId,
    PeppolAdapter,
    TransmissionRequest,
    TransmissionResult,
)
from app.services.peppol_adapters.dispatcher import register_peppol_adapter

logger = logging.getLogger(__name__)

TIMEOUT = 20.0


@register_peppol_adapter("as4_gateway")
class AS4GatewayAdapter(PeppolAdapter):
    provider_name = "as4_gateway"

    def __init__(self, config: dict):
        super().__init__(config)
        # Per-org config wins; fall back to the process-level setting. The API
        # key has NO hardcoded fallback — empty means "not configured".
        self.gateway_url: str = (config.get("gateway_url") or settings.peppol_gateway_url).rstrip(
            "/"
        )
        self.api_key: str = config.get("api_key") or settings.peppol_gateway_api_key

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def resolve_participant(self, participant_id: ParticipantId) -> ParticipantCapability:
        if not self.api_key or not self.gateway_url:
            return ParticipantCapability(
                participant_id=participant_id,
                registered=False,
                unregistered_reason="peppol_not_configured",
            )
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                response = await client.get(
                    f"{self.gateway_url}/smp/participants/{participant_id.format()}",
                    headers=self._headers(),
                )
        except httpx.RequestError as exc:
            # PII-free: class name only, never the participant value.
            return ParticipantCapability(
                participant_id=participant_id,
                registered=False,
                unregistered_reason=f"gateway_transport_error:{exc.__class__.__name__}",
            )
        if response.status_code == 404:
            return ParticipantCapability(
                participant_id=participant_id,
                registered=False,
                unregistered_reason="receiver_not_registered",
            )
        if response.status_code >= 400:
            return ParticipantCapability(
                participant_id=participant_id,
                registered=False,
                unregistered_reason=f"gateway_error:{response.status_code}",
            )
        data = response.json() or {}
        return ParticipantCapability(
            participant_id=participant_id,
            registered=bool(data.get("registered", True)),
            access_point_url=data.get("access_point_url"),
            supported_doc_types=tuple(data.get("supported_doc_types") or ()),
        )

    async def send(self, request: TransmissionRequest) -> TransmissionResult:
        if not self.api_key or not self.gateway_url:
            return TransmissionResult(
                success=False,
                status="failed",
                failure_reason="peppol_not_configured",
            )
        # The gateway builds the SBDH from this metadata + the UBL payload. We
        # base64 the UBL so it travels safely in JSON. business_message_id is
        # sent as the gateway-level idempotency key (defence in depth; the DB
        # unique index is the authoritative one-time guarantee).
        body = {
            "sender": request.sender.format(),
            "receiver": request.receiver.format(),
            "doc_type_id": request.doc_type_id,
            "process_id": request.process_id,
            "business_message_id": request.business_message_id,
            "payload_base64": base64.b64encode(request.payload).decode("ascii"),
        }
        headers = {**self._headers(), "Idempotency-Key": request.business_message_id}
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                response = await client.post(
                    f"{self.gateway_url}/as4/outbound",
                    json=body,
                    headers=headers,
                )
        except httpx.RequestError as exc:
            return TransmissionResult(
                success=False,
                status="failed",
                failure_reason=f"gateway_transport_error:{exc.__class__.__name__}",
            )
        if response.status_code >= 400:
            try:
                err = response.json() or {}
            except ValueError:
                err = {}
            # PII-free: the gateway's error code / status, never the payload.
            return TransmissionResult(
                success=False,
                status="failed",
                failure_reason=f"gateway_error:{err.get('code') or response.status_code}",
            )
        data = response.json() or {}
        status = data.get("status", "sent")
        return TransmissionResult(
            success=status != "failed",
            message_id=data.get("message_id"),
            status=status,
            failure_reason=data.get("failure_code") if status == "failed" else None,
            raw_response=data,
        )

    async def test_connection(self) -> bool:
        if not self.api_key or not self.gateway_url:
            return False
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                response = await client.get(
                    f"{self.gateway_url}/health",
                    headers=self._headers(),
                )
        except httpx.RequestError:
            return False
        return response.status_code < 400

    def parse_inbound(self, headers: dict, body: bytes):
        # Inbound-ready stub — the next slice verifies the gateway HMAC
        # (webhook_security.verify_hmac_sha256) and dedupes by AS4 MessageId.
        return None
