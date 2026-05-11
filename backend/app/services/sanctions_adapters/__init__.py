"""Sanctions-screening adapters — pluggable providers for OFAC / EU /
UN / UK consolidated-list checks plus PEP screening.

Same registry pattern as `card_adapters`, `extraction_adapters`,
`erp_adapters`, `audit_shipping`, `fx_adapters`. Default in local dev
is `mock`; production deployments configure
`Organization.settings.compliance.sanctions.provider` to one of the
registered names (today: `mock`, `complyadvantage` skeleton).
"""

from app.services.sanctions_adapters.base import SanctionsAdapter, ScreeningResult
from app.services.sanctions_adapters.dispatcher import (
    get_sanctions_adapter,
    register_sanctions_adapter,
)

__all__ = [
    "SanctionsAdapter",
    "ScreeningResult",
    "get_sanctions_adapter",
    "register_sanctions_adapter",
]
