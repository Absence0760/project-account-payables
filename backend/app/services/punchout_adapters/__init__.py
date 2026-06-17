"""Punch-out catalog adapters — live cXML/OCI round-trips.

Mirrors ``peppol_adapters``: a registry decorator, an in-process ``mock``
default (local-first, no supplier/network) and a real ``cxml`` adapter that
builds/parses real cXML and fails closed (``punchout_not_configured``) when the
supplier shared secret is absent. Selection via
``Organization.settings.punchout.provider`` → ``AP_PUNCHOUT_PROVIDER`` (default
``mock``). See ``docs/procurement-catalogs.md``.
"""

# Import adapters so they self-register with the dispatcher.
from app.services.punchout_adapters import cxml_adapter as _cxml  # noqa: F401
from app.services.punchout_adapters import mock_adapter as _mock  # noqa: F401
from app.services.punchout_adapters.base import (
    PunchoutAdapter,
    PunchoutCart,
    PunchoutCartItem,
    PunchoutError,
    PunchoutSetupContext,
    PunchoutStartResult,
)
from app.services.punchout_adapters.dispatcher import (
    get_punchout_adapter,
    list_available_providers,
    register_punchout_adapter,
)

__all__ = [
    "PunchoutAdapter",
    "PunchoutCart",
    "PunchoutCartItem",
    "PunchoutError",
    "PunchoutSetupContext",
    "PunchoutStartResult",
    "get_punchout_adapter",
    "list_available_providers",
    "register_punchout_adapter",
]
