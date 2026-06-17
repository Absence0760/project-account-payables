"""Mock QMS adapter — deterministic for tests and local dev.

The safe local-first default: no network, no credential, no randomness.
``fetch_inspections`` returns a small fixed set of inspection records (a
``pass``, a ``fail``, and a ``partial``) so a test — or a repeated
``pnpm dev`` run — sees the same records every time and the sync service's
idempotent upsert can be exercised end to end.

The fixed records reference ``po_number`` / ``gr_number`` values that the
seed data uses (``PO-1001`` / ``GR-1001``); when those documents don't
exist in a given tenant the sync service simply lands the inspection with
NULL ``po_id`` / ``gr_id`` (resolution is best-effort, never a hard error).

A test can override the returned set via
``qms_config["mock_records"]`` (a list of dicts shaped like a
``QMSInspectionRecord``'s fields).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from app.services.qms_adapters.base import QMSInspectionRecord
from app.services.qms_adapters.dispatcher import register_qms_adapter

_DEFAULT_RECORDS: list[QMSInspectionRecord] = [
    QMSInspectionRecord(
        inspection_number="QMS-INSP-001",
        result="pass",
        po_number="PO-1001",
        gr_number="GR-1001",
        inspected_date=date(2024, 1, 15),
        inspector="QMS Auto",
        accepted_quantity=Decimal("100.0000"),
        rejected_quantity=Decimal("0.0000"),
        deviation_notes=None,
        raw={"source": "mock", "disposition": "accept"},
    ),
    QMSInspectionRecord(
        inspection_number="QMS-INSP-002",
        result="fail",
        po_number="PO-1002",
        gr_number=None,
        inspected_date=date(2024, 1, 16),
        inspector="QMS Auto",
        accepted_quantity=Decimal("0.0000"),
        rejected_quantity=Decimal("50.0000"),
        deviation_notes="Surface finish out of spec on full lot.",
        raw={"source": "mock", "disposition": "reject"},
    ),
    QMSInspectionRecord(
        inspection_number="QMS-INSP-003",
        result="partial",
        po_number="PO-1003",
        gr_number="GR-1003",
        inspected_date=date(2024, 1, 17),
        inspector="QMS Auto",
        accepted_quantity=Decimal("80.0000"),
        rejected_quantity=Decimal("20.0000"),
        deviation_notes="Partial acceptance — 20 units dimensionally out of tolerance.",
        raw={"source": "mock", "disposition": "partial"},
    ),
]


@register_qms_adapter("mock")
class MockQMSAdapter:
    provider_name = "mock"

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self._records = self._build_records(self.config.get("mock_records"))

    @staticmethod
    def _build_records(raw_records: list | None) -> list[QMSInspectionRecord]:
        if not raw_records:
            return list(_DEFAULT_RECORDS)
        records: list[QMSInspectionRecord] = []
        for r in raw_records:
            inspected = r.get("inspected_date")
            if isinstance(inspected, str):
                inspected = date.fromisoformat(inspected)
            accepted = r.get("accepted_quantity")
            rejected = r.get("rejected_quantity")
            records.append(
                QMSInspectionRecord(
                    inspection_number=r["inspection_number"],
                    result=r.get("result", "pass"),
                    po_number=r.get("po_number"),
                    gr_number=r.get("gr_number"),
                    inspected_date=inspected,
                    inspector=r.get("inspector"),
                    accepted_quantity=Decimal(str(accepted)) if accepted is not None else None,
                    rejected_quantity=Decimal(str(rejected)) if rejected is not None else None,
                    deviation_notes=r.get("deviation_notes"),
                    raw=r.get("raw") or {"source": "mock"},
                )
            )
        return records

    async def fetch_inspections(
        self, *, since: datetime | None = None
    ) -> list[QMSInspectionRecord]:
        # The mock has no real change-feed; ``since`` is ignored and the
        # full deterministic set is returned. The sync service upserts
        # idempotently, so re-returning the same rows is harmless.
        return list(self._records)

    async def test_connection(self) -> bool:
        return True
