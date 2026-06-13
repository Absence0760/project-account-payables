"""Base PEPPOL Access Point adapter interface + value objects.

PEPPOL is a four-corner network: sender (C1) → sender Access Point (C2) →
receiver Access Point (C3) → receiver (C4). We are C1; we integrate with a
*hosted* Access Point (C2) over its HTTP API. We do NOT implement raw
AS4/ebMS3 SOAP here — the adapter speaks the gateway's API and the gateway
does the AS4 handshake to C3. The `mock` adapter simulates the AP entirely
in-process (no DNS, no SMP, no network) so local dev needs no credential.

PII invariant: a PEPPOL participant `value` is the counterparty's
organisation / tax id, which lives legitimately inside the UBL payload and on
the transmission row — but NEVER in a log line or an HTTP error body. The
``ParticipantId.format()`` string therefore must not be logged at INFO, and
:class:`PeppolSendError` carries only a PII-free reason *code*.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Wire prefix for an ISO 6523 actor id (the only scheme PEPPOL uses).
_PEPPOL_ID_PREFIX = "iso6523-actorid-upis"

# An EAS scheme code is a short run of digits (e.g. "9930", "0088", "0192").
_SCHEME_RE = re.compile(r"^[0-9]{1,20}$")


class PeppolSendError(Exception):
    """Raised by the send service when a transmission cannot proceed.

    Carries a PII-free reason *code* only (e.g. ``receiver_not_registered``,
    ``peppol_not_configured``) — never the participant value, a tax id, or an
    address. ``str(exc)`` is therefore always safe in an HTTP error body.
    """

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ParticipantId:
    """PEPPOL participant identifier — EAS scheme + value.

    Wire form: ``iso6523-actorid-upis::<scheme>:<value>``, e.g.
    ``iso6523-actorid-upis::9930:DE123456789``. ``scheme`` is the EAS code
    (0088=GLN, 9930=DE org, 0192=NO org); ``value`` is the registered id.

    PII note: a tax / org id lives in ``value`` legitimately; never log the
    formatted string at INFO and never embed ``value`` in an error message.
    """

    scheme: str  # EAS code, digits, e.g. "9930"
    value: str  # registered id, e.g. "DE123456789"

    @classmethod
    def parse(cls, raw: str) -> ParticipantId:
        """Parse a participant id from either wire form.

        Accepts ``iso6523-actorid-upis::9930:DE123456789`` OR the bare
        ``9930:DE123456789``. Raises a PII-free :class:`ValueError` that names
        the field but NEVER echoes the value, so a malformed id can be reported
        without the (possibly sensitive) value entering a log or error body.
        """
        if not raw or not isinstance(raw, str):
            raise ValueError("participant_id: missing")
        text = raw.strip()
        # Strip the optional iso6523 prefix ("scheme::scheme:value").
        if text.startswith(f"{_PEPPOL_ID_PREFIX}::"):
            text = text[len(_PEPPOL_ID_PREFIX) + 2 :]
        scheme, sep, value = text.partition(":")
        if not sep or not scheme or not value:
            # Name the field only — do not echo `raw` (PII).
            raise ValueError("participant_id: malformed")
        if not _SCHEME_RE.match(scheme):
            raise ValueError("participant_id.scheme: malformed")
        return cls(scheme=scheme, value=value)

    def format(self) -> str:
        """Render the full wire form ``iso6523-actorid-upis::<scheme>:<value>``."""
        return f"{_PEPPOL_ID_PREFIX}::{self.scheme}:{self.value}"

    def __str__(self) -> str:
        # NB: contains the PII value — never pass to a logger at INFO.
        return self.format()


@dataclass
class ParticipantCapability:
    """Result of SMP/SML resolution for a receiver participant."""

    participant_id: ParticipantId
    registered: bool
    access_point_url: str | None = None  # C3 endpoint
    supported_doc_types: tuple[str, ...] = ()  # docType ids the receiver accepts
    unregistered_reason: str | None = None  # PII-free code, set when registered is False


@dataclass
class TransmissionRequest:
    """Everything the adapter needs to transmit one UBL document."""

    sender: ParticipantId  # C1 (us)
    receiver: ParticipantId  # C4 (the supplier / customer)
    doc_type_id: str  # PEPPOL_BIS_BILLING_DOCTYPE
    process_id: str  # PEPPOL_BIS_BILLING_PROCESSID
    payload: bytes  # UBL 2.1 from generate_ubl(doc)
    business_message_id: str  # our idempotency key (= invoice.correlation_id hex)


@dataclass
class TransmissionResult:
    success: bool
    message_id: str | None = None  # AP-assigned message id (C2→C3 AS4 MessageId)
    status: str = "sent"  # "sent" | "delivered" | "failed"
    failure_reason: str | None = None  # PII-free code, e.g. "receiver_not_registered"
    raw_response: dict | None = field(default=None)


class PeppolAdapter:
    """Base class for PEPPOL Access Point integrations.

    Adapters are stateless — anything tenant-specific lives in ``self.config``.
    """

    provider_name: str = "base"

    def __init__(self, config: dict):
        self.config = config

    async def resolve_participant(self, participant_id: ParticipantId) -> ParticipantCapability:
        """SMP/SML lookup: is the receiver registered, and on which AP?"""
        raise NotImplementedError

    async def send(self, request: TransmissionRequest) -> TransmissionResult:
        """Transmit one UBL document to the receiver via the Access Point.

        Idempotent at the gateway by ``request.business_message_id`` (defence in
        depth — the authoritative one-time guarantee is the DB unique index on
        the transmission row)."""
        raise NotImplementedError

    async def test_connection(self) -> bool:
        """Cheap credential / reachability check."""
        raise NotImplementedError

    # INBOUND-ready (next slice) — default raises; mock/gateway implement later.
    def parse_inbound(self, headers: dict, body: bytes):
        """Parse + verify an inbound AS4 delivery from the Access Point.

        The next (inbound) slice reuses ``services/webhook_security.py`` to
        verify the gateway's HMAC and dedupe by the AS4 ``MessageId`` (the same
        shape as payment webhooks dedupe by ``event_id``)."""
        raise NotImplementedError
