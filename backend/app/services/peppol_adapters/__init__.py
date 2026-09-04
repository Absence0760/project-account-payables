"""PEPPOL Access Point adapters — outbound e-invoice transmission (four-corner).

Mirrors ``payment_adapters``: a registry decorator, an in-process ``mock``
default (local-first, no network) and a real ``as4_gateway`` adapter that
talks to a hosted Access Point's HTTP API. See ``docs/peppol.md``.
"""

# Import adapters so they self-register with the dispatcher.
from app.services.peppol_adapters import as4_gateway as _as4_gateway  # noqa: F401
from app.services.peppol_adapters import mock_adapter as _mock  # noqa: F401
from app.services.peppol_adapters.base import (
    ParticipantCapability,
    ParticipantId,
    PeppolAdapter,
    PeppolSendError,
    TransmissionRequest,
    TransmissionResult,
)
from app.services.peppol_adapters.constants import (
    PEPPOL_BIS_BILLING_DOCTYPE,
    PEPPOL_BIS_BILLING_PROCESSID,
)
from app.services.peppol_adapters.dispatcher import (
    UnknownPeppolProviderError,
    get_peppol_adapter,
    list_available_providers,
    register_peppol_adapter,
)

__all__ = [
    "PEPPOL_BIS_BILLING_DOCTYPE",
    "PEPPOL_BIS_BILLING_PROCESSID",
    "ParticipantCapability",
    "ParticipantId",
    "PeppolAdapter",
    "PeppolSendError",
    "TransmissionRequest",
    "TransmissionResult",
    "UnknownPeppolProviderError",
    "get_peppol_adapter",
    "list_available_providers",
    "register_peppol_adapter",
]
