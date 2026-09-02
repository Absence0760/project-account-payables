"""`GET /api/payments/queue` is paginated, and `/queue/ids` resolves the whole
selectable set for "select all N matching".

The queue used to return the tenant's entire approved-unpaid invoice set on
every page view (persona-power-user, issue #328). It now returns one
`page`/`page_size` page with an `id`-tie-broken order, while the money totals
and the `total` / `selectable_total` / `blocked_total` counts still describe
the WHOLE queue — a KPI/banner over one page would contradict the list.

Requires the dev Postgres (`pnpm db:up`).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.entity import Entity
from app.models.exception import Exception as ExceptionModel
from app.models.invoice import Invoice, InvoiceStatus

pytestmark = pytest.mark.asyncio

TENANT = "a"
SEEDED = 25  # > the 20-row page


async def _default_entity_id(s) -> uuid.UUID:
    return (await s.execute(select(Entity.id).where(Entity.is_default))).scalar_one()


async def _seed_queue(mk, org_id, *, prefix: str, n: int, currency: str = "USD") -> list[str]:
    numbers = [f"{prefix}-{i:03d}" for i in range(n)]
    async with mk() as s:
        ent = await _default_entity_id(s)
        for num in numbers:
            s.add(
                Invoice(
                    id=uuid.uuid4(),
                    organization_id=org_id,
                    entity_id=ent,
                    invoice_number=num,
                    vendor_name="Queue Pager",
                    amount=Decimal("10.00"),
                    currency=currency,
                    status=InvoiceStatus.approved,
                    correlation_id=uuid.uuid4(),
                )
            )
        await s.commit()
    return numbers


async def test_queue_pages_and_ids_resolver(realdb):
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    seeded = await _seed_queue(mk, org_id, prefix="QP", n=SEEDED)

    async with realdb.client(key=TENANT, role="admin") as c:
        p1 = (await c.get("/api/payments/queue", params={"page": 1, "page_size": 20})).json()
        p2 = (await c.get("/api/payments/queue", params={"page": 2, "page_size": 20})).json()
        ids_resp = (await c.get("/api/payments/queue/ids")).json()

    # Page 1 is capped; `total` is the whole set (plus any pre-existing seed rows).
    assert len(p1["items"]) == 20
    assert p1["total"] >= SEEDED
    assert p1["page"] == 1 and p1["page_size"] == 20

    # Page 2 appends the tail with no id appearing twice across the pages.
    n1 = {i["invoice_number"] for i in p1["items"]}
    n2 = {i["invoice_number"] for i in p2["items"]}
    assert not (n1 & n2), "a row appeared on both pages — missing id tie-breaker"
    assert n1 | n2 >= set(seeded[:40])  # every seeded row reachable within 2 pages
    ids1 = [i["id"] for i in p1["items"]]
    ids2 = [i["id"] for i in p2["items"]]
    assert len(set(ids1 + ids2)) == len(ids1) + len(ids2)

    # `/queue/ids` resolves the WHOLE selectable set, not one page.
    assert ids_resp["total"] >= SEEDED
    assert len(ids_resp["ids"]) == ids_resp["total"]  # under the cap
    assert ids_resp["truncated"] is False
    assert set(ids1) <= set(ids_resp["ids"])
    # Per-currency breakdown covers the whole selectable set.
    usd = next(b for b in ids_resp["by_currency"] if b["currency"] == "USD")
    assert usd["count"] >= SEEDED
    assert Decimal(usd["total_amount"]) >= Decimal("10.00") * SEEDED


async def test_blocked_row_stays_blocked_and_is_excluded_from_ids(realdb):
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    seeded = await _seed_queue(mk, org_id, prefix="QPB", n=3)

    # Block the first one with a payment-blocking exception.
    async with mk() as s:
        blocked_inv = (
            await s.execute(select(Invoice.id).where(Invoice.invoice_number == seeded[0]))
        ).scalar_one()
        s.add(
            ExceptionModel(
                id=uuid.uuid4(),
                invoice_id=blocked_inv,
                exception_type="duplicate",
                severity="error",
                status="open",
                organization_id=org_id,
            )
        )
        await s.commit()

    async with realdb.client(key=TENANT, role="admin") as c:
        page = (await c.get("/api/payments/queue", params={"page": 1, "page_size": 100})).json()
        ids_resp = (await c.get("/api/payments/queue/ids")).json()

    by_num = {i["invoice_number"]: i for i in page["items"]}
    assert by_num[seeded[0]]["blocked"] is True
    assert by_num[seeded[0]]["blocked_reason"] == "duplicate"
    assert by_num[seeded[1]]["blocked"] is False

    # blocked_total / selectable_total split the whole queue.
    assert page["total"] == page["selectable_total"] + page["blocked_total"]
    assert page["blocked_total"] >= 1

    # The blocked id is not in the "select all matching" set.
    assert str(blocked_inv) not in ids_resp["ids"]


async def test_queue_requires_a_payments_role(realdb):
    """`/queue/ids` inherits the same RBAC as `/queue` — an ap_clerk is refused."""
    async with realdb.client(key=TENANT, role="ap_clerk") as c:
        assert (await c.get("/api/payments/queue")).status_code == 403
        assert (await c.get("/api/payments/queue/ids")).status_code == 403
