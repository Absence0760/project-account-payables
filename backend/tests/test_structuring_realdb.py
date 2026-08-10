"""Real-DB coverage for the structuring guard (`services/structuring.py`).

`vendor_recent_spend` sums a vendor's OTHER recent invoices so
`services.review._enforce_approval_thresholds` can escalate on the aggregate
even when no single invoice crosses the max/CFO gate alone (splitting one big
payable into several smaller ones). Three invariants a fraud-detection
aggregate must hold, none of them exercised by the mock-`db` unit tests in
`test_approval_thresholds.py`:

  1. The rolling window is keyed off `Invoice.created_at` (server-stamped,
     non-null), never `invoice_date` (nullable, and populated straight from
     the vendor's own bill / AI extraction) — a structuring vendor is exactly
     who'd omit or backdate that field to keep a split invoice off the
     aggregate.
  2. The sum never mixes currencies — a EUR and a USD invoice for the same
     vendor are not added as equal face values.
  3. The sum is scoped to the evaluated invoice's own entity — a subsidiary's
     spend never inflates a sibling's aggregate.

DO NOT run this file standalone in a concurrent build — the ``realdb`` fixture
truncates all tables sequentially. The orchestrator runs the suite at the end.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.models.invoice import Invoice
from app.models.vendor import Vendor
from app.services.structuring import vendor_recent_spend


def _u() -> str:
    return uuid.uuid4().hex[:8]


async def _mk_vendor(realdb, key="a", *, vendor_id=None) -> uuid.UUID:
    vendor_id = vendor_id or uuid.uuid4()
    mk = realdb.sessionmaker(key)
    org_id = realdb.info(key).org_id
    async with mk() as s:
        s.add(Vendor(id=vendor_id, name="Acme Supply", organization_id=org_id, status="active"))
        await s.commit()
    return vendor_id


async def _mk_invoice(
    realdb,
    key="a",
    *,
    vendor_id,
    amount,
    currency="USD",
    entity_id=None,
    created_at=None,
) -> uuid.UUID:
    mk = realdb.sessionmaker(key)
    org_id = realdb.info(key).org_id
    iid = uuid.uuid4()
    async with mk() as s:
        invoice = Invoice(
            id=iid,
            invoice_number=f"INV-{_u()}",
            vendor_name="Acme Supply",
            vendor_id=vendor_id,
            amount=Decimal(amount),
            status="new",
            currency=currency,
            entity_id=entity_id,
            organization_id=org_id,
        )
        if created_at is not None:
            invoice.created_at = created_at
        s.add(invoice)
        await s.commit()
    return iid


async def test_recent_spend_ignores_null_invoice_date(realdb):
    """A just-submitted invoice with no `invoice_date` (a hand-keyed split
    invoice with nothing extracted yet) still counts toward the window — the
    aggregate is keyed off `created_at`, not the vendor-supplied date."""
    vendor_id = await _mk_vendor(realdb)
    await _mk_invoice(realdb, vendor_id=vendor_id, amount="12000.00")

    async with realdb.sessionmaker("a")() as db:
        total = await vendor_recent_spend(
            db, vendor_id=vendor_id, exclude_invoice_id=None, window_days=30, currency="USD"
        )
    assert total == Decimal("12000.00")


async def test_recent_spend_excludes_rows_outside_window_by_created_at(realdb):
    """A row stamped outside the window at `created_at` is excluded even
    though it has no `invoice_date` to fall back on."""
    vendor_id = await _mk_vendor(realdb)
    stale = datetime.now(UTC) - timedelta(days=60)
    await _mk_invoice(realdb, vendor_id=vendor_id, amount="12000.00", created_at=stale)

    async with realdb.sessionmaker("a")() as db:
        total = await vendor_recent_spend(
            db, vendor_id=vendor_id, exclude_invoice_id=None, window_days=30, currency="USD"
        )
    assert total == Decimal("0")


async def test_recent_spend_excludes_foreign_currency_invoices(realdb):
    """A USD aggregate never sums a same-vendor invoice denominated in
    another currency — the legs don't convert."""
    vendor_id = await _mk_vendor(realdb)
    await _mk_invoice(realdb, vendor_id=vendor_id, amount="300.00", currency="USD")
    await _mk_invoice(realdb, vendor_id=vendor_id, amount="999.00", currency="EUR")

    async with realdb.sessionmaker("a")() as db:
        total = await vendor_recent_spend(
            db, vendor_id=vendor_id, exclude_invoice_id=None, window_days=30, currency="USD"
        )
    assert total == Decimal("300.00")


async def test_recent_spend_respects_entity_scope(realdb):
    """An entity-scoped evaluation never picks up a sibling entity's spend
    for the same vendor."""
    from app.models.entity import Entity

    org_id = realdb.info("a").org_id
    other_entity_id = uuid.uuid4()
    async with realdb.sessionmaker("a")() as s:
        s.add(
            Entity(
                id=other_entity_id,
                organization_id=org_id,
                name="Other Sub",
                slug=f"other-{_u()}",
                currency="USD",
                is_default=False,
                is_active=True,
            )
        )
        await s.commit()

    vendor_id = await _mk_vendor(realdb)
    await _mk_invoice(realdb, vendor_id=vendor_id, amount="300.00", entity_id=other_entity_id)
    await _mk_invoice(realdb, vendor_id=vendor_id, amount="999.00", entity_id=None)

    async with realdb.sessionmaker("a")() as db:
        total = await vendor_recent_spend(
            db,
            vendor_id=vendor_id,
            exclude_invoice_id=None,
            window_days=30,
            currency="USD",
            entity_id=other_entity_id,
        )
    assert total == Decimal("300.00")


async def test_recent_spend_unscoped_when_entity_id_none(realdb):
    """A None `entity_id` (invoice never stamped to a subsidiary) is
    consolidated — same semantics as `apply_entity_scope` everywhere else."""
    vendor_id = await _mk_vendor(realdb)
    await _mk_invoice(realdb, vendor_id=vendor_id, amount="300.00", entity_id=None)

    async with realdb.sessionmaker("a")() as db:
        total = await vendor_recent_spend(
            db,
            vendor_id=vendor_id,
            exclude_invoice_id=None,
            window_days=30,
            currency="USD",
            entity_id=None,
        )
    assert total == Decimal("300.00")
