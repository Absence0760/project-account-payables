"""Quality Management System (QMS) adapter contract.

A QMS (the system a plant / warehouse uses to record incoming-goods
quality inspections — e.g. ETQ, MasterControl, an SAP QM module, or a
home-grown LIMS) is the upstream source of the 4-way-match leg this app
stores as :class:`~app.models.quality_inspection.QualityInspection`.

The adapter has one read-side job: pull inspection records out of the
external QMS so the sync service can land them in ``quality_inspections``
keyed on ``(organization_id, inspection_number)``. It never writes back
to the QMS.

All quantity fields are ``Decimal`` (project invariant — never ``float``
for accepted/rejected counts). The provider's raw payload is preserved on
``raw`` so an auditor can replay it. Config comes from
``Organization.settings.qms`` — see the dispatcher.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True)
class QMSInspectionRecord:
    """One quality-inspection record as the external QMS reports it.

    ``inspection_number`` is the QMS's own identifier and is the stable
    idempotency key the sync service upserts on. ``po_number`` /
    ``gr_number`` are the *external* document references the sync service
    resolves to local ``PurchaseOrder`` / ``GoodsReceipt`` ids (a QMS
    speaks document numbers, not our internal UUIDs).

    ``result`` is one of ``pass`` / ``fail`` / ``partial`` (the same
    vocabulary :class:`QualityInspection` uses). ``accepted_quantity`` /
    ``rejected_quantity`` are ``Decimal`` quantities. ``raw`` keeps the
    provider's untouched record for audit replay.
    """

    inspection_number: str
    result: str
    po_number: str | None = None
    gr_number: str | None = None
    inspected_date: date | None = None
    inspector: str | None = None
    accepted_quantity: Decimal | None = None
    rejected_quantity: Decimal | None = None
    deviation_notes: str | None = None
    raw: dict = field(default_factory=dict)


class QMSAdapter(Protocol):
    """The minimum contract every QMS provider satisfies."""

    provider_name: str

    async def fetch_inspections(
        self, *, since: datetime | None = None
    ) -> list[QMSInspectionRecord]:
        """Return inspection records, optionally only those changed after
        ``since`` (an incremental-pull hint — implementations that can't
        filter server-side may ignore it and return the full set; the sync
        service upserts idempotently either way)."""
        ...

    async def test_connection(self) -> bool:
        """Cheapest available probe (auth check). True on success."""
        ...
