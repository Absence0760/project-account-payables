"""Real-DB coverage for the expense-preapproval router.

Covers create (requester stamped from the caller), list + filters, the
approve / reject decisions, segregation of duties (a user can't decide their own
request), invalid-state guards, RBAC, and audit rows. Mirrors the ``realdb``
idioms in ``tests/test_expenses.py``.
"""

import uuid

from sqlalchemy import select

from app.models.expense import ExpensePreapproval
from app.models.workflow import AuditLog


async def test_create_preapproval_stamps_requester(realdb):
    mk = realdb.sessionmaker("a")
    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await c.post(
            "/api/expense-preapprovals",
            json={
                "title": "Conference travel",
                "estimated_amount": "1200.00",
                "category": "travel",
            },
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "pending"
    assert body["estimated_amount"] == 1200.0
    assert body["requester_user_id"] == str(realdb.info("a").users["ap_clerk"])

    async with mk() as s:
        row = (await s.execute(select(ExpensePreapproval))).scalar_one()
        assert row.requester_user_id == realdb.info("a").users["ap_clerk"]
        actions = (
            (
                await s.execute(
                    select(AuditLog.action).where(AuditLog.entity_type == "expense_preapproval")
                )
            )
            .scalars()
            .all()
        )
        assert "expense_preapproval.created" in actions


async def test_list_filters_by_status(realdb):
    async with realdb.client(key="a", role="ap_clerk") as c:
        await c.post(
            "/api/expense-preapprovals",
            json={"title": "A", "estimated_amount": "10.00"},
        )
        pending = await c.get("/api/expense-preapprovals?status=pending")
        assert pending.status_code == 200
        assert pending.json()["total"] >= 1
        approved = await c.get("/api/expense-preapprovals?status=approved")
        assert approved.json()["total"] == 0


async def test_manager_approves_clerk_request(realdb):
    mk = realdb.sessionmaker("a")
    async with realdb.client(key="a", role="ap_clerk") as c:
        pid = (
            await c.post(
                "/api/expense-preapprovals",
                json={"title": "Laptop", "estimated_amount": "2000.00"},
            )
        ).json()["id"]
    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(f"/api/expense-preapprovals/{pid}/approve")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "approved"
    assert body["decided_by"] == str(realdb.info("a").users["ap_manager"])
    assert body["decided_at"]

    async with mk() as s:
        actions = (
            (
                await s.execute(
                    select(AuditLog.action).where(AuditLog.entity_type == "expense_preapproval")
                )
            )
            .scalars()
            .all()
        )
        assert "expense_preapproval.approved" in actions


async def test_self_approval_blocked_by_segregation(realdb):
    # The same clerk who raised the request tries to approve it → 403.
    # (clerk lacks approve RBAC, so use admin who is both requester + approver.)
    async with realdb.client(key="a", role="admin") as c:
        pid = (
            await c.post(
                "/api/expense-preapprovals",
                json={"title": "Self", "estimated_amount": "500.00"},
            )
        ).json()["id"]
        resp = await c.post(f"/api/expense-preapprovals/{pid}/approve")
    assert resp.status_code == 403
    assert "segregation" in resp.json()["detail"].lower()


async def test_reject_path(realdb):
    async with realdb.client(key="a", role="ap_clerk") as c:
        pid = (
            await c.post(
                "/api/expense-preapprovals",
                json={"title": "Denied", "estimated_amount": "9000.00"},
            )
        ).json()["id"]
    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(
            f"/api/expense-preapprovals/{pid}/reject", json={"reason": "over budget"}
        )
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"


async def test_double_decision_blocked(realdb):
    async with realdb.client(key="a", role="ap_clerk") as c:
        pid = (
            await c.post(
                "/api/expense-preapprovals",
                json={"title": "Once", "estimated_amount": "100.00"},
            )
        ).json()["id"]
    async with realdb.client(key="a", role="ap_manager") as c:
        first = await c.post(f"/api/expense-preapprovals/{pid}/approve")
        assert first.status_code == 200
        second = await c.post(f"/api/expense-preapprovals/{pid}/reject")
    assert second.status_code == 422


async def test_clerk_cannot_approve(realdb):
    async with realdb.client(key="a", role="ap_manager") as c:
        pid = (
            await c.post(
                "/api/expense-preapprovals",
                json={"title": "X", "estimated_amount": "10.00"},
            )
        ).json()["id"]
    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await c.post(f"/api/expense-preapprovals/{pid}/approve")
    assert resp.status_code == 403


async def test_tenant_isolation(realdb):
    async with realdb.client(key="a", role="ap_clerk") as c:
        pid = (
            await c.post(
                "/api/expense-preapprovals",
                json={"title": "X", "estimated_amount": "10.00"},
            )
        ).json()["id"]
    async with realdb.client(key="b", role="ap_manager") as c:
        assert (await c.get(f"/api/expense-preapprovals/{pid}")).status_code == 404


async def test_unknown_preapproval_404(realdb):
    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(f"/api/expense-preapprovals/{uuid.uuid4()}/approve")
    assert resp.status_code == 404
