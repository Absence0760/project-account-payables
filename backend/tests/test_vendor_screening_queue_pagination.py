"""`GET /api/vendors/screening/review-queue` pagination + the whole-set counts
that replaced deriving KPIs from the loaded queue.

The queue returned a bare unbounded list, and `/vendors/screening` computed its
"Sanctions matches" / "Needs review" headline figures by filtering that list.
Those figures were correct only because the endpoint happened to return every
row and happened to select on exactly those two statuses — a property of the
implementation, not of the contract, and the first page of results would have
silently turned both into undercounts.

Two things are pinned here:

* the queue pages deterministically — no row dropped, none duplicated, across
  a boundary where the sort key (`last_screened_at`) is deliberately a tie;
* `GET /api/vendors/counts` reports `by_screening_status` over the WHOLE set,
  so the KPIs no longer depend on how much of the queue is loaded.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.models.vendor import Vendor

TENANT = "a"


async def _seed_flagged(mk, org_id, n: int, *, screening_status: str, screened_at) -> set[str]:
    """`n` vendors flagged for review, ALL sharing one `last_screened_at`.

    The shared timestamp is the point: it is exactly what a bulk re-screen
    produces, and it is the case an `ORDER BY last_screened_at` with no
    tie-break gets wrong — Postgres may order equal keys differently between
    the `offset=0` and `offset=N` queries.
    """
    ids: set[str] = set()
    async with mk() as s:
        for i in range(n):
            vid = uuid.uuid4()
            ids.add(str(vid))
            s.add(
                Vendor(
                    id=vid,
                    name=f"Queue Page Co {i:02d}",
                    organization_id=org_id,
                    status="active",
                    source="manual",
                    screening_status=screening_status,
                    last_screened_at=screened_at,
                )
            )
        await s.commit()
    return ids


@pytest.mark.asyncio
async def test_queue_pages_without_dropping_or_duplicating_a_row(realdb):
    info = realdb.info(TENANT)
    mk = realdb.sessionmaker(TENANT)
    same_moment = datetime.now(UTC)
    seeded = await _seed_flagged(
        mk, info.org_id, 7, screening_status="review", screened_at=same_moment
    )
    seeded |= await _seed_flagged(
        mk, info.org_id, 5, screening_status="match", screened_at=same_moment
    )

    async with realdb.client(key=TENANT, role="ap_manager") as client:
        first = await client.get("/api/vendors/screening/review-queue?page=1&page_size=5")
        second = await client.get("/api/vendors/screening/review-queue?page=2&page_size=5")
        third = await client.get("/api/vendors/screening/review-queue?page=3&page_size=5")

    assert first.status_code == 200, first.text
    body = first.json()
    assert body["page"] == 1 and body["page_size"] == 5
    assert body["total"] >= 12
    assert len(body["items"]) == 5

    pages = [first.json()["items"], second.json()["items"], third.json()["items"]]
    seen = [it["vendor_id"] for page in pages for it in page]
    assert len(seen) == len(set(seen)), "a vendor was served on two pages"
    assert seeded <= set(seen), "a vendor fell between two pages"


@pytest.mark.asyncio
async def test_queue_ordering_is_stable_across_repeated_reads(realdb):
    """Same request, same order — the `.id` tie-break is what guarantees it
    when every row shares a `last_screened_at`."""
    info = realdb.info(TENANT)
    mk = realdb.sessionmaker(TENANT)
    await _seed_flagged(
        mk, info.org_id, 9, screening_status="review", screened_at=datetime.now(UTC)
    )

    async with realdb.client(key=TENANT, role="ap_manager") as client:
        runs = []
        for _ in range(3):
            resp = await client.get("/api/vendors/screening/review-queue?page=2&page_size=4")
            assert resp.status_code == 200, resp.text
            runs.append([it["vendor_id"] for it in resp.json()["items"]])

    assert runs[0] == runs[1] == runs[2]


@pytest.mark.asyncio
async def test_counts_report_screening_buckets_over_the_whole_set(realdb):
    """The KPI figures must not depend on how much of the queue is loaded."""
    info = realdb.info(TENANT)
    mk = realdb.sessionmaker(TENANT)
    now = datetime.now(UTC)
    await _seed_flagged(mk, info.org_id, 6, screening_status="match", screened_at=now)
    await _seed_flagged(mk, info.org_id, 4, screening_status="review", screened_at=now)
    await _seed_flagged(mk, info.org_id, 3, screening_status="clear", screened_at=now)

    async with realdb.client(key=TENANT, role="ap_manager") as client:
        counts = (await client.get("/api/vendors/counts")).json()
        page = (await client.get("/api/vendors/screening/review-queue?page=1&page_size=2")).json()

    buckets = counts["by_screening_status"]
    assert buckets["match"] >= 6
    assert buckets["review"] >= 4
    assert buckets["clear"] >= 3
    # The figure the page shows is whole-set, and the loaded page is not.
    assert len(page["items"]) == 2
    assert buckets["match"] + buckets["review"] == page["total"], (
        "the two screening buckets are exactly the queue's population — "
        "if that stops being true the KPI and the table describe different sets"
    )
    # `by_status` is a DIFFERENT axis (Vendor.status) and must still sum to
    # `total` on its own; the two groupings share one aggregate pass.
    assert sum(counts["by_status"].values()) == counts["total"]
    assert sum(buckets.values()) == counts["total"]


@pytest.mark.asyncio
async def test_counts_screening_buckets_honour_the_search_filter(realdb):
    """`by_screening_status` rides the SAME `_vendor_list_filters` population as
    `by_status`, so a narrowed list narrows the KPI with it."""
    info = realdb.info(TENANT)
    mk = realdb.sessionmaker(TENANT)
    now = datetime.now(UTC)
    await _seed_flagged(mk, info.org_id, 3, screening_status="match", screened_at=now)

    async with realdb.client(key=TENANT, role="ap_manager") as client:
        narrowed = (await client.get("/api/vendors/counts?search=NoSuchVendorNameAtAll")).json()

    assert narrowed["total"] == 0
    assert narrowed["by_screening_status"] == {}
