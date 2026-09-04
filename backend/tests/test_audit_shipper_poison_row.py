"""One unshippable audit row must not stop a tenant's WORM trail.

The shipper's batch is all-or-nothing and ordered `created_at ASC`, so a single
row a sink refuses — `details` is free-form JSONB — made `adapter.ship` raise on
every tick, re-select the identical oldest-first batch, and block every NEWER
row for that tenant forever. `ShipResult.failures` climbed and the sweep went
`degraded`, which is correct; the defect was that the only remedy was manual.

The fix is a bounded isolation pass: a failed batch is re-shipped row by row,
and a row an adapter refuses is re-offered to THAT adapter with a PII-free
quarantine marker in place of its details. A sink that refuses even the marker
is unhealthy rather than poisoned, so the pass stops there and the tick fails
exactly as before — that is what stops an outage from stripping the details off
a whole batch a healthy sink would have taken.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.services import audit_log_shipper
from app.services.audit_log_shipper import QUARANTINE_KEY, ShipResult, _quarantined_row
from app.services.audit_shipping import AuditLogRow, AuditShippingAdapter

# Stands in for the vendor / account fragment a sink's error message can carry.
# It must never reach a log record or the WORM store.
_PII_SENTINEL = "SECRET_ACCOUNT_1234567890"


class _RejectsPoisonAdapter(AuditShippingAdapter):
    """Refuses any row whose `details` carries `poison`; takes everything else,
    including the quarantine marker."""

    provider_name = "rejects-poison"

    def __init__(self) -> None:
        super().__init__({})
        self.shipped: list[AuditLogRow] = []
        self.calls = 0

    async def ship(self, rows: list[AuditLogRow]) -> None:
        self.calls += 1
        for row in rows:
            if (row.details or {}).get("poison"):
                raise RuntimeError(f"sink refused the row payload: {_PII_SENTINEL}")
        self.shipped.extend(rows)

    async def test_connection(self) -> bool:
        return True


class _OutageAdapter(AuditShippingAdapter):
    """Refuses everything — a sink outage, not a poison row."""

    provider_name = "outage"

    def __init__(self) -> None:
        super().__init__({})
        self.calls = 0

    async def ship(self, rows: list[AuditLogRow]) -> None:
        self.calls += 1
        raise RuntimeError(f"sink unavailable: {_PII_SENTINEL}")

    async def test_connection(self) -> bool:
        return False


async def _add_rows(realdb, key: str, details: list[dict]) -> list[uuid.UUID]:
    """Insert one unshipped audit row per `details` entry, oldest first."""
    from app.models.workflow import AuditLog

    org_id = realdb.info(key).org_id
    base = datetime.now(UTC) - timedelta(minutes=len(details) + 1)
    ids: list[uuid.UUID] = []
    async with realdb.sessionmaker(key)() as s:
        for i, detail in enumerate(details):
            row = AuditLog(
                id=uuid.uuid4(),
                correlation_id=uuid.uuid4(),
                organization_id=org_id,
                actor_id=None,
                action="invoice.approved",
                entity_type="invoice",
                entity_id=uuid.uuid4(),
                details=detail,
                created_at=base + timedelta(minutes=i),
            )
            s.add(row)
            ids.append(row.id)
        await s.commit()
    return ids


async def _shipped_at(realdb, key: str, ids: list[uuid.UUID]) -> dict[uuid.UUID, object]:
    from app.models.workflow import AuditLog

    async with realdb.sessionmaker(key)() as s:
        rows = (
            await s.execute(select(AuditLog.id, AuditLog.shipped_at).where(AuditLog.id.in_(ids)))
        ).all()
    return {rid: stamp for rid, stamp in rows}


# ---------------------------------------------------------------------------
# Pure — the marker itself
# ---------------------------------------------------------------------------


def test_quarantine_marker_keeps_identity_and_drops_the_refused_payload():
    row = AuditLogRow(
        id=uuid.uuid4(),
        tenant_db="feoh_x",
        organization_id=uuid.uuid4(),
        correlation_id=uuid.uuid4(),
        actor_id=None,
        action="invoice.approved",
        entity_type="invoice",
        entity_id=uuid.uuid4(),
        details={"bank_account": _PII_SENTINEL, "poison": True},
        created_at=datetime.now(UTC),
    )
    marked = _quarantined_row(row, ValueError("boom " + _PII_SENTINEL))

    # Identity survives — that is what keeps the WORM copy an ordered trail.
    assert marked.id == row.id
    assert marked.action == row.action
    assert marked.entity_id == row.entity_id
    assert marked.created_at == row.created_at
    # The refused payload does not, and neither does the exception's message.
    assert marked.details[QUARANTINE_KEY] is True
    assert marked.details["error_class"] == "ValueError"
    assert marked.details["original_bytes"] > 0
    assert _PII_SENTINEL not in str(marked.details)
    assert "poison" not in marked.details
    assert row.details == {"bank_account": _PII_SENTINEL, "poison": True}  # not mutated


# ---------------------------------------------------------------------------
# realdb — the head-of-line block itself
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poison_row_is_quarantined_and_newer_rows_ship(realdb, caplog):
    """The row the sink refuses is shipped with a marker, and the rows AFTER it
    ship on the SAME tick. Pre-fix nothing after it ever shipped."""
    ids = await _add_rows(realdb, "a", [{"seq": 0}, {"poison": True, "seq": 1}, {"seq": 2}])
    adapter = _RejectsPoisonAdapter()
    result = ShipResult()

    with caplog.at_level(logging.WARNING, logger=audit_log_shipper.logger.name):
        shipped = await audit_log_shipper._ship_tenant(
            realdb.info("a").db_name, [adapter], result=result
        )

    assert shipped == 3
    assert result.rows_quarantined == 1

    stamps = await _shipped_at(realdb, "a", ids)
    assert all(v is not None for v in stamps.values()), "every row moved, poison included"

    by_id = {r.id: r for r in adapter.shipped}
    assert set(by_id) == set(ids)
    # The two healthy rows reached the sink untouched...
    assert by_id[ids[0]].details == {"seq": 0}
    assert by_id[ids[2]].details == {"seq": 2}
    # ...and the poison row reached it as an unmistakable, PII-free marker.
    marker = by_id[ids[1]].details
    assert marker[QUARANTINE_KEY] is True
    assert marker["reason"] == "sink_rejected_row"
    assert marker["error_class"] == "RuntimeError"
    assert "poison" not in marker

    # The sink's error text never reaches the log sink (PII-out-of-logs).
    for record in caplog.records:
        assert _PII_SENTINEL not in record.getMessage()
    assert any(QUARANTINE_KEY in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_sink_outage_is_never_quarantined(realdb, caplog):
    """A sink that refuses the marker too is unhealthy, not poisoned: nothing is
    stamped, nothing is stripped, the tick fails, and the probing is bounded."""
    ids = await _add_rows(realdb, "a", [{"seq": 0}, {"seq": 1}, {"seq": 2}])
    adapter = _OutageAdapter()
    result = ShipResult()

    with caplog.at_level(logging.WARNING, logger=audit_log_shipper.logger.name):
        with pytest.raises(RuntimeError):
            await audit_log_shipper._ship_tenant(realdb.info("a").db_name, [adapter], result=result)

    stamps = await _shipped_at(realdb, "a", ids)
    assert all(v is None for v in stamps.values()), "an outage must not consume the batch"
    assert result.rows_quarantined == 0
    # Batch attempt + the first row + that row's marker, then it gives up: an
    # outage costs two extra calls, not one per row.
    assert adapter.calls == 3
    for record in caplog.records:
        assert _PII_SENTINEL not in record.getMessage()


@pytest.mark.asyncio
async def test_progress_before_a_fatal_row_is_still_stamped(realdb):
    """Rows the isolation pass got through are stamped even though the pass then
    hit a row no sink would take — otherwise they'd be re-shipped forever."""

    class _RejectsSecond(AuditShippingAdapter):
        provider_name = "rejects-second"

        def __init__(self) -> None:
            super().__init__({})
            self.target: uuid.UUID | None = None

        async def ship(self, rows: list[AuditLogRow]) -> None:
            if any(r.id == self.target for r in rows):
                raise RuntimeError("permanently refused")

        async def test_connection(self) -> bool:
            return True

    ids = await _add_rows(realdb, "a", [{"seq": 0}, {"seq": 1}, {"seq": 2}])
    adapter = _RejectsSecond()
    adapter.target = ids[1]
    result = ShipResult()

    with pytest.raises(RuntimeError, match="permanently refused"):
        await audit_log_shipper._ship_tenant(realdb.info("a").db_name, [adapter], result=result)

    stamps = await _shipped_at(realdb, "a", ids)
    assert stamps[ids[0]] is not None  # made progress
    assert stamps[ids[1]] is None  # the fatal row stays for the next tick
    assert stamps[ids[2]] is None  # ordering preserved: nothing jumps it
    assert result.rows_quarantined == 0


@pytest.mark.asyncio
async def test_quarantine_is_per_adapter(realdb):
    """A row CloudWatch refuses may be fine for the S3 copy — only the refusing
    sink gets the marker; the healthy one keeps the full details."""
    ids = await _add_rows(realdb, "a", [{"poison": True, "seq": 0}])
    picky = _RejectsPoisonAdapter()

    class _AcceptsEverything(AuditShippingAdapter):
        provider_name = "accepts"

        def __init__(self) -> None:
            super().__init__({})
            self.shipped: list[AuditLogRow] = []

        async def ship(self, rows: list[AuditLogRow]) -> None:
            self.shipped.extend(rows)

        async def test_connection(self) -> bool:
            return True

    tolerant = _AcceptsEverything()
    result = ShipResult()

    shipped = await audit_log_shipper._ship_tenant(
        realdb.info("a").db_name, [tolerant, picky], result=result
    )

    assert shipped == 1
    assert result.rows_quarantined == 1
    assert all((r.details or {}).get("poison") for r in tolerant.shipped)
    assert [r.details[QUARANTINE_KEY] for r in picky.shipped] == [True]
    assert (await _shipped_at(realdb, "a", ids))[ids[0]] is not None
