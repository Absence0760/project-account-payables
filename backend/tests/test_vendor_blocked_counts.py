"""`GET /api/vendors/counts` — the payment-block tally.

The `/vendors/screening` page's "Payments blocked" KPI used to be derived on
the client from the SCREENING REVIEW QUEUE
(`Vendor.screening_status IN ('match','review')`). But
`POST /api/vendors/{id}/block` sets `Vendor.payments_blocked` and deliberately
never touches `screening_status`, so a vendor AP blocked while screening-clear
belonged to no bucket the queue selects on — the headline claiming to count
blocked payments could not see it, at any page size, ever. That is the failure
`docs/decisions.md` §48 is about: a tally must be computed from a query that
asks the tally's own question, not read off a set selected on a different
column.

The fix is `payments_blocked` on the counts endpoint, riding the SAME
aggregate (and therefore the same `_vendor_list_filters` predicates and the
same entity scope) as the status buckets beside it.

Every assertion here fails against the previous implementation, where the key
simply did not exist in the response body.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models.entity import Entity
from app.models.vendor import Vendor

TENANT = "a"


@pytest.fixture
def mk(realdb):
    return realdb.sessionmaker(TENANT)


async def _default_entity_id(session) -> uuid.UUID:
    return (await session.execute(select(Entity.id).where(Entity.is_default))).scalar_one()


async def _second_entity_id(session, org_id: uuid.UUID) -> uuid.UUID:
    """A non-default second subsidiary in this tenant, created once."""
    existing = (
        await session.execute(select(Entity.id).where(Entity.slug == "blocked-counts-sub"))
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    entity = Entity(
        name="Blocked Counts Sub",
        slug="blocked-counts-sub",
        organization_id=org_id,
        is_default=False,
    )
    session.add(entity)
    await session.flush()
    return entity.id


# ===========================================================================
# 1. A vendor blocked while screening-clear IS counted.
# ===========================================================================


@pytest.mark.asyncio
async def test_blocked_but_screening_clear_vendor_is_counted(realdb, mk):
    """The regression. Block a vendor through the real endpoint — which leaves
    `screening_status` alone — and the tally must still see it.

    The pre-fix page counted `items.filter(it => it.payments_blocked)` over the
    review queue, and this vendor is not in the review queue at all.
    """
    org_id = realdb.info(TENANT).org_id
    async with mk() as s:
        entity_id = await _default_entity_id(s)
        clear = Vendor(
            name="Clear But Blocked Co",
            code="CBB001",
            status="active",
            screening_status="clear",
            organization_id=org_id,
            entity_id=entity_id,
        )
        # A second, untouched vendor proves the tally is not just "every row".
        s.add(clear)
        s.add(
            Vendor(
                name="Ordinary Co",
                code="ORD001",
                status="active",
                screening_status="clear",
                organization_id=org_id,
                entity_id=entity_id,
            )
        )
        await s.commit()
        blocked_id = str(clear.id)

    async with realdb.client(key=TENANT, role="admin") as client:
        before = (await client.get("/api/vendors/counts")).json()
        assert before["payments_blocked"] == 0, before

        resp = await client.post(f"/api/vendors/{blocked_id}/block", json={"reason": "AP hold"})
        assert resp.status_code == 200, resp.text
        # The block endpoint really does leave the screening verdict alone —
        # which is exactly why the review queue cannot see this vendor.
        assert resp.json()["screening_status"] == "clear"
        assert resp.json()["payments_blocked"] is True

        after = (await client.get("/api/vendors/counts")).json()

    assert after["payments_blocked"] == 1, after
    # `payments_blocked` is an orthogonal axis, never a slice of `by_status` —
    # the blocked vendor is still an `active` one and `total` is unchanged.
    assert after["total"] == before["total"] == 2
    assert after["by_status"] == {"active": 2}


@pytest.mark.asyncio
async def test_unblock_clears_the_tally(realdb, mk):
    """The tally is computed on read, so lifting a block lowers it — no stored
    running total to drift out of step."""
    org_id = realdb.info(TENANT).org_id
    async with mk() as s:
        entity_id = await _default_entity_id(s)
        v = Vendor(
            name="Toggle Co",
            code="TOG001",
            status="active",
            screening_status="clear",
            payments_blocked=True,
            organization_id=org_id,
            entity_id=entity_id,
        )
        s.add(v)
        await s.commit()
        vid = str(v.id)

    async with realdb.client(key=TENANT, role="admin") as client:
        assert (await client.get("/api/vendors/counts")).json()["payments_blocked"] == 1
        assert (await client.post(f"/api/vendors/{vid}/unblock", json={})).status_code == 200
        assert (await client.get("/api/vendors/counts")).json()["payments_blocked"] == 0


# ===========================================================================
# 2. The tally takes the list's filters — exactly as its sibling buckets do.
# ===========================================================================


@pytest.mark.asyncio
async def test_blocked_tally_honours_search_and_source(realdb, mk):
    """A "Payments blocked" figure must not silently span a wider population
    than the one the caller asked about (§48). It rides the same
    `_vendor_list_filters` call as `by_status`, so `search` and `source` narrow
    both together."""
    org_id = realdb.info(TENANT).org_id
    async with mk() as s:
        entity_id = await _default_entity_id(s)
        for name, code, source, blocked in (
            ("Findable Blocked Co", "FBC1", "manual", True),
            ("Findable Allowed Co", "FAC1", "manual", False),
            ("Findable Synced Co", "FSC1", "erp_sync", True),
            ("Hidden Blocked Co", "HBC1", "manual", True),
        ):
            s.add(
                Vendor(
                    name=name,
                    code=code,
                    status="active",
                    screening_status="clear",
                    source=source,
                    payments_blocked=blocked,
                    organization_id=org_id,
                    entity_id=entity_id,
                )
            )
        await s.commit()

    async with realdb.client(key=TENANT, role="admin") as client:
        whole = (await client.get("/api/vendors/counts")).json()
        searched = (await client.get("/api/vendors/counts?search=Findable")).json()
        sourced = (await client.get("/api/vendors/counts?search=Findable&source=manual")).json()
        # §48 part 2: `status` is the dimension the endpoint tallies, so it is
        # NOT a declared parameter — FastAPI drops it and the tally is
        # unchanged. Asserted here so the blocked figure can't quietly start
        # honouring it and zero itself for every non-matching chip.
        with_status = (await client.get("/api/vendors/counts?search=Findable&status=active")).json()

    assert whole["payments_blocked"] == 3
    # `Hidden Blocked Co` drops out with the search.
    assert searched["payments_blocked"] == 2
    assert searched["total"] == 3
    # `Findable Synced Co` drops out with the source filter.
    assert sourced["payments_blocked"] == 1
    assert sourced["total"] == 2
    assert with_status == searched


# ===========================================================================
# 3. Entity scoping — a blocked subsidiary vendor must not leak into a
#    sibling's KPI.
# ===========================================================================


@pytest.mark.asyncio
async def test_blocked_tally_is_entity_scoped(realdb, mk):
    """`vendors` carries a nullable `entity_id`, and every read on this
    endpoint goes through `apply_entity_scope`. The blocked tally is inside the
    SAME aggregate, so it is scoped by construction — pinned here because a
    figure describing another subsidiary's payment blocks is exactly the
    cross-entity leak entity scoping exists to prevent."""
    org_id = realdb.info(TENANT).org_id
    async with mk() as s:
        default_id = await _default_entity_id(s)
        other_id = await _second_entity_id(s, org_id)
        s.add(
            Vendor(
                name="Default Entity Blocked",
                code="DEB1",
                status="active",
                screening_status="clear",
                payments_blocked=True,
                organization_id=org_id,
                entity_id=default_id,
            )
        )
        for i in range(2):
            s.add(
                Vendor(
                    name=f"Sub Entity Blocked {i}",
                    code=f"SEB{i}",
                    status="active",
                    screening_status="clear",
                    payments_blocked=True,
                    organization_id=org_id,
                    entity_id=other_id,
                )
            )
        await s.commit()
        default_header = str(default_id)
        other_header = str(other_id)

    async with realdb.client(key=TENANT, role="admin") as client:
        in_default = (
            await client.get("/api/vendors/counts", headers={"X-Entity-ID": default_header})
        ).json()
        in_other = (
            await client.get("/api/vendors/counts", headers={"X-Entity-ID": other_header})
        ).json()

    assert in_default["payments_blocked"] == 1, in_default
    assert in_other["payments_blocked"] == 2, in_other


# ===========================================================================
# RBAC — §48 requires the tally's gate to match its list's EXACTLY, and adding
# a field must not have widened or narrowed it.
# ===========================================================================


@pytest.mark.parametrize("role", ["admin", "ap_manager", "cfo"])
@pytest.mark.asyncio
async def test_counts_readable_by_every_role_that_reads_the_vendor_list(realdb, role):
    async with realdb.client(key=TENANT, role=role) as client:
        listed = await client.get("/api/vendors")
        counted = await client.get("/api/vendors/counts")
    assert counted.status_code == listed.status_code == 200
    assert "payments_blocked" in counted.json()


@pytest.mark.asyncio
async def test_counts_refuses_exactly_where_the_vendor_list_refuses(realdb):
    """`ap_clerk` cannot read the vendor list, so it cannot read its tally
    either — a tally reachable by more callers than the rows it counts
    discloses the size of a set they cannot see (§48)."""
    async with realdb.client(key=TENANT, role="ap_clerk") as client:
        listed = await client.get("/api/vendors")
        counted = await client.get("/api/vendors/counts")
    assert listed.status_code == 403
    assert counted.status_code == 403
