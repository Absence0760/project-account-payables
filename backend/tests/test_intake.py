"""Real-DB coverage for the procurement intake router.

Covers ``backend/app/api/intake.py`` end-to-end against the live test tenants:
intake CRUD, the flexible ``form_data`` JSONB round-trip, the status
transitions (open→in_review→approved/rejected, cancel), convert-to-requisition
(creates a ``PurchaseRequisition`` + line, sets the intake to ``converted`` +
stamps ``converted_requisition_id``, idempotent), RBAC, tenant isolation, audit
rows, and exact ``Numeric`` money round-trips.

Request numbers are uuid-suffixed so re-runs against the persistent test tenant
never collide on a stale row.
"""

import uuid
from decimal import Decimal

from sqlalchemy import select

from app.models.procurement import (
    IntakeRequest,
    IntakeStatus,
    PurchaseRequisition,
    RequisitionLineItem,
)
from app.models.workflow import AuditLog


def _num(prefix: str = "INTK") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _payload(**over) -> dict:
    body = {
        "request_number": _num(),
        "title": "Figma Enterprise seats",
        "request_type": "software",
        "estimated_amount": "1200.00",
        "currency": "USD",
        "vendor_name": "Figma Inc",
        "justification": "Design team scaling",
    }
    body.update(over)
    return body


# ---------------------------------------------------------------------------
# CRUD + form_data round-trip
# ---------------------------------------------------------------------------


async def test_requester_is_the_caller_not_a_client_supplied_id(realdb):
    """`requester_user_id` is the SoD anchor a converted requisition inherits,
    so it cannot be the creator's own input.

    `convert_intake_to_requisition` copies it verbatim onto the
    `PurchaseRequisition`, and `POST /api/requisitions/{id}/approve` compares
    exactly that field against the approver. Accepting it from the body let a
    single ap_manager raise an intake "for" an arbitrary uuid, convert it, and
    then approve the resulting requisition themselves — no accomplice, no
    second role. `POST /api/requisitions` already hard-sets the requester to
    the caller; this path must agree.
    """
    planted = str(uuid.uuid4())
    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await c.post("/api/intake", json=_payload(requester_user_id=planted))
    assert resp.status_code == 201, resp.text
    assert resp.json()["requester_user_id"] != planted


async def test_create_intake(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id

    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await c.post("/api/intake", json=_payload())
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["title"] == "Figma Enterprise seats"
    assert body["request_type"] == "software"
    assert body["estimated_amount"] == 1200.0
    assert body["status"] == "open"
    assert body["requester_user_id"]  # defaulted to the caller

    async with mk() as s:
        row = (
            await s.execute(select(IntakeRequest).where(IntakeRequest.id == uuid.UUID(body["id"])))
        ).scalar_one()
        assert row.estimated_amount == Decimal("1200.00")  # exact Numeric round-trip
        assert row.organization_id == org_id
        actions = (
            (
                await s.execute(
                    select(AuditLog.action).where(AuditLog.entity_type == "intake_request")
                )
            )
            .scalars()
            .all()
        )
        assert "intake.created" in actions


async def test_create_generates_request_number_when_omitted(realdb):
    async with realdb.client(key="a", role="ap_clerk") as c:
        body = _payload()
        del body["request_number"]
        resp = await c.post("/api/intake", json=body)
    assert resp.status_code == 201, resp.text
    assert resp.json()["request_number"].startswith("INTK-")


async def test_form_data_jsonb_roundtrip(realdb):
    mk = realdb.sessionmaker("a")
    form = {
        "seats": 25,
        "renewal": "annual",
        "data_residency": "US",
        "integrations": ["sso", "scim"],
    }
    async with realdb.client(key="a", role="ap_clerk") as c:
        created = (await c.post("/api/intake", json=_payload(form_data=form))).json()
        # Survives the GET round-trip intact.
        fetched = (await c.get(f"/api/intake/{created['id']}")).json()
    assert fetched["form_data"] == form

    async with mk() as s:
        row = (
            await s.execute(
                select(IntakeRequest).where(IntakeRequest.id == uuid.UUID(created["id"]))
            )
        ).scalar_one()
        assert row.form_data == form  # exact JSONB persisted


async def test_list_filter_and_search(realdb):
    async with realdb.client(key="a", role="ap_clerk") as c:
        sw = (await c.post("/api/intake", json=_payload(request_type="software"))).json()
        await c.post(
            "/api/intake", json=_payload(request_type="services", title="Pen-test engagement")
        )

        # Type filter.
        listing = await c.get("/api/intake?type=software")
        assert listing.status_code == 200
        assert all(i["request_type"] == "software" for i in listing.json()["items"])

        # Search by title.
        found = await c.get("/api/intake?search=Pen-test")
        assert found.json()["total"] >= 1
        assert any("Pen-test" in i["title"] for i in found.json()["items"])

        # Status filter.
        opened = await c.get("/api/intake?status=open")
        assert any(i["id"] == sw["id"] for i in opened.json()["items"])


async def test_update_open_intake(realdb):
    mk = realdb.sessionmaker("a")
    async with realdb.client(key="a", role="ap_clerk") as c:
        iid = (await c.post("/api/intake", json=_payload())).json()["id"]
        resp = await c.patch(
            f"/api/intake/{iid}",
            json={"title": "Figma Org plan", "estimated_amount": "2400.00"},
        )
    assert resp.status_code == 200
    assert resp.json()["title"] == "Figma Org plan"
    assert resp.json()["estimated_amount"] == 2400.0

    async with mk() as s:
        row = (
            await s.execute(select(IntakeRequest).where(IntakeRequest.id == uuid.UUID(iid)))
        ).scalar_one()
        assert row.estimated_amount == Decimal("2400.00")


async def test_update_blocked_after_review(realdb):
    async with realdb.client(key="a", role="ap_manager") as c:
        iid = (await c.post("/api/intake", json=_payload())).json()["id"]
        await c.post(f"/api/intake/{iid}/submit")  # open → in_review
        resp = await c.patch(f"/api/intake/{iid}", json={"title": "nope"})
    assert resp.status_code == 422  # only editable while open


async def test_delete_intake(realdb):
    async with realdb.client(key="a", role="ap_clerk") as c:
        iid = (await c.post("/api/intake", json=_payload())).json()["id"]
        gone = await c.delete(f"/api/intake/{iid}")
        assert gone.status_code == 204
        missing = await c.get(f"/api/intake/{iid}")
        assert missing.status_code == 404


# ---------------------------------------------------------------------------
# Status transitions
# ---------------------------------------------------------------------------


async def test_submit_approve_flow(realdb):
    mk = realdb.sessionmaker("a")
    async with realdb.client(key="a", role="ap_clerk") as c:
        iid = (await c.post("/api/intake", json=_payload())).json()["id"]
        submitted = await c.post(f"/api/intake/{iid}/submit")
        assert submitted.json()["status"] == "in_review"

    async with realdb.client(key="a", role="ap_manager") as c:
        approved = await c.post(f"/api/intake/{iid}/approve", json={"reason": "looks good"})
        assert approved.json()["status"] == "approved"

    async with mk() as s:
        actions = (
            (
                await s.execute(
                    select(AuditLog.action).where(AuditLog.entity_type == "intake_request")
                )
            )
            .scalars()
            .all()
        )
        assert "intake.submitted" in actions
        assert "intake.approved" in actions


async def test_reject_stamps_reason_and_form_data(realdb):
    mk = realdb.sessionmaker("a")
    async with realdb.client(key="a", role="ap_manager") as c:
        iid = (await c.post("/api/intake", json=_payload(form_data={"seats": 5}))).json()["id"]
        await c.post(f"/api/intake/{iid}/submit")
        rejected = await c.post(f"/api/intake/{iid}/reject", json={"reason": "use existing tool"})
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["form_data"]["review_reason"] == "use existing tool"
    assert rejected.json()["form_data"]["seats"] == 5  # original keys preserved

    async with mk() as s:
        row = (
            await s.execute(select(IntakeRequest).where(IntakeRequest.id == uuid.UUID(iid)))
        ).scalar_one()
        assert row.status == IntakeStatus.rejected


async def test_cancel_intake(realdb):
    async with realdb.client(key="a", role="ap_clerk") as c:
        iid = (await c.post("/api/intake", json=_payload())).json()["id"]
        cancelled = await c.post(f"/api/intake/{iid}/cancel")
    assert cancelled.json()["status"] == "cancelled"


async def test_invalid_transition_is_422(realdb):
    async with realdb.client(key="a", role="ap_manager") as c:
        iid = (await c.post("/api/intake", json=_payload())).json()["id"]
        # Approve straight from open (must go through in_review first) → 422.
        resp = await c.post(f"/api/intake/{iid}/approve")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Convert to requisition (+ idempotency)
# ---------------------------------------------------------------------------


async def test_convert_to_requisition(realdb):
    mk = realdb.sessionmaker("a")
    async with realdb.client(key="a", role="ap_manager") as c:
        iid = (await c.post("/api/intake", json=_payload(estimated_amount="3000.00"))).json()["id"]
        await c.post(f"/api/intake/{iid}/submit")
        await c.post(f"/api/intake/{iid}/approve")
        converted = await c.post(f"/api/intake/{iid}/convert-to-requisition")
    assert converted.status_code == 200, converted.text
    body = converted.json()
    assert body["created"] is True
    assert body["intake"]["status"] == "converted"
    assert body["intake"]["converted_requisition_id"] == body["requisition_id"]
    req_id = body["requisition_id"]

    async with mk() as s:
        req = (
            await s.execute(
                select(PurchaseRequisition).where(PurchaseRequisition.id == uuid.UUID(req_id))
            )
        ).scalar_one()
        assert req.total == Decimal("3000.00")  # exact money carried over
        assert req.title == "Figma Enterprise seats"
        lines = (
            (
                await s.execute(
                    select(RequisitionLineItem).where(
                        RequisitionLineItem.requisition_id == uuid.UUID(req_id)
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(lines) == 1
        assert lines[0].total == Decimal("3000.00")
        actions = (
            (
                await s.execute(
                    select(AuditLog.action).where(
                        AuditLog.action == "intake.converted_to_requisition"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(actions) >= 1


async def test_convert_is_idempotent(realdb):
    """A second convert returns the existing requisition (created=False) and
    never creates a second one — a double click can't double-spend."""
    mk = realdb.sessionmaker("a")
    async with realdb.client(key="a", role="ap_manager") as c:
        iid = (await c.post("/api/intake", json=_payload())).json()["id"]
        await c.post(f"/api/intake/{iid}/submit")
        await c.post(f"/api/intake/{iid}/approve")
        first = (await c.post(f"/api/intake/{iid}/convert-to-requisition")).json()
        second = (await c.post(f"/api/intake/{iid}/convert-to-requisition")).json()

    assert first["created"] is True
    assert second["created"] is False
    assert first["requisition_id"] == second["requisition_id"]

    async with mk() as s:
        count = len(
            (
                await s.execute(
                    select(PurchaseRequisition.id).where(
                        PurchaseRequisition.id == uuid.UUID(first["requisition_id"])
                    )
                )
            )
            .scalars()
            .all()
        )
        assert count == 1


async def test_convert_requires_approved(realdb):
    async with realdb.client(key="a", role="ap_manager") as c:
        iid = (await c.post("/api/intake", json=_payload())).json()["id"]
        # Still open — cannot convert.
        resp = await c.post(f"/api/intake/{iid}/convert-to-requisition")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# RBAC + tenant isolation
# ---------------------------------------------------------------------------


async def test_clerk_cannot_approve(realdb):
    async with realdb.client(key="a", role="ap_clerk") as c:
        iid = (await c.post("/api/intake", json=_payload())).json()["id"]
        await c.post(f"/api/intake/{iid}/submit")
        resp = await c.post(f"/api/intake/{iid}/approve")
    assert resp.status_code == 403  # approve is reviewer-only


async def test_clerk_cannot_convert(realdb):
    async with realdb.client(key="a", role="ap_manager") as c:
        iid = (await c.post("/api/intake", json=_payload())).json()["id"]
        await c.post(f"/api/intake/{iid}/submit")
        await c.post(f"/api/intake/{iid}/approve")
    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await c.post(f"/api/intake/{iid}/convert-to-requisition")
    assert resp.status_code == 403


async def test_cfo_can_create_and_read(realdb):
    # Intake is broad-access — the CFO can raise a request.
    async with realdb.client(key="a", role="cfo") as c:
        resp = await c.post("/api/intake", json=_payload())
        assert resp.status_code == 201
        listing = await c.get("/api/intake")
        assert listing.status_code == 200


async def test_tenant_isolation(realdb):
    async with realdb.client(key="a", role="ap_manager") as c:
        iid = (await c.post("/api/intake", json=_payload())).json()["id"]
    async with realdb.client(key="b", role="ap_manager") as c:
        assert (await c.get(f"/api/intake/{iid}")).status_code == 404
