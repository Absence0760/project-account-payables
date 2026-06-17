"""Quality Management System (QMS) adapters — pluggable connectors that pull
inspection records from an external QMS / LIMS into the local
``quality_inspections`` table (the 4-way-match leg).

Same registry pattern as ``financing_adapters``, ``fx_adapters``,
``sanctions_adapters``. Default in local dev is ``mock`` (deterministic, no
network, no credential); production deployments configure
``Organization.settings.qms.provider`` to a registered name (today: ``mock``,
``generic`` skeleton — live REST endpoint + key required).
"""

from app.services.qms_adapters.base import QMSAdapter, QMSInspectionRecord
from app.services.qms_adapters.dispatcher import get_qms_adapter, register_qms_adapter

__all__ = [
    "QMSAdapter",
    "QMSInspectionRecord",
    "get_qms_adapter",
    "register_qms_adapter",
]
