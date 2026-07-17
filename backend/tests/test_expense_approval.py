"""Real-DB coverage for the WF3 expense-report approval workflow + policy CRUD
wiring.

Covers:
- policy CRUD (create / list / patch / delete, RBAC, audit)
- policy violations surfaced on expense create + cleared on receipt upload
- report submit blocks on a BLOCKING violation (missing required receipt)
- report submit succeeds when clean, stamps submitted_at + child statuses
- approve self-blocked by segregation of duties
- CFO-threshold role gating (default 5000 + a custom org override)
- reject path returns children to draft
- invalid-state guards (422)
- every transition is audited

Mirrors the ``realdb`` idioms in ``tests/test_expenses.py``.
"""

import uuid
from decimal import Decimal

from sqlalchemy import select

from app.models.expense import Expense, ExpensePolicy
from app.models.organization import Organization
from app.models.workflow import AuditLog

# ---------------------------------------------------------------------------
# Policy CRUD
# ---------------------------------------------------------------------------


async def test_policy_crud(realdb):
    mk = realdb.sessionmaker("a")
    async with realdb.client(key="a", role="ap_manager") as c:
        created = await c.post(
            "/api/expense-policies",
            json={
                "name": "Travel policy",
                "category": "travel",
                "category_limit": "100.00",
                "requires_receipt_above": "25.00",
                "mileage_rate": "0.6700",
            },
        )
        assert created.status_code == 201, created.text
        pid = created.json()["id"]
        assert created.json()["category_limit"] == 100.0
        assert created.json()["mileage_rate"] == 0.67

        listing = await c.get("/api/expense-policies?active=true")
        assert listing.status_code == 200
        assert listing.json()["total"] >= 1

        patched = await c.patch(f"/api/expense-policies/{pid}", json={"category_limit": "200.00"})
        assert patched.status_code == 200
        assert patched.json()["category_limit"] == 200.0

        deleted = await c.delete(f"/api/expense-policies/{pid}")
        assert deleted.status_code == 204

    async with mk() as s:
        actions = (
            (
                await s.execute(
                    select(AuditLog.action).where(AuditLog.entity_type == "expense_policy")
                )
            )
            .scalars()
            .all()
        )
        assert "expense_policy.created" in actions
        assert "expense_policy.updated" in actions
        assert "expense_policy.deleted" in actions


async def test_policy_clerk_cannot_mutate(realdb):
    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await c.post("/api/expense-policies", json={"name": "x", "category_limit": "10.00"})
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Policy violations surfaced on the expense
# ---------------------------------------------------------------------------


async def test_violation_surfaced_on_create_and_cleared_on_receipt(realdb):
    async with realdb.client(key="a", role="ap_manager") as c:
        await c.post(
            "/api/expense-policies",
            json={
                "name": "Receipt policy",
                "category": "travel",
                "requires_receipt_above": "10.00",
            },
        )
        created = await c.post(
            "/api/expenses",
            json={"expense_date": "2026-06-01", "category": "travel", "amount": "50.00"},
        )
        eid = created.json()["id"]
        violations = created.json()["policy_violations"]
        assert violations and violations[0]["code"] == "receipt_required"

        # Uploading a receipt clears the receipt_required violation.
        up = await c.post(
            f"/api/expenses/{eid}/receipt",
            files={"file": ("r.pdf", b"%PDF-1.4 fake", "application/pdf")},
        )
        assert up.status_code == 200, up.text
        assert not up.json()["policy_violations"]


# ---------------------------------------------------------------------------
# Report submit
# ---------------------------------------------------------------------------


async def _make_policy(c, **kw):
    body = {"name": kw.pop("name", "P")}
    body.update(kw)
    return (await c.post("/api/expense-policies", json=body)).json()


async def _make_report_with_expense(c, amount="100.00", category="travel", receipt=False):
    eid = (
        await c.post(
            "/api/expenses",
            json={"expense_date": "2026-06-01", "category": category, "amount": amount},
        )
    ).json()["id"]
    if receipt:
        await c.post(
            f"/api/expenses/{eid}/receipt",
            files={"file": ("r.pdf", b"%PDF-1.4 fake", "application/pdf")},
        )
    rid = (
        await c.post(
            "/api/expense-reports",
            json={"report_number": f"R-{uuid.uuid4().hex[:8]}"},
        )
    ).json()["id"]
    await c.post(f"/api/expense-reports/{rid}/expenses", json={"expense_ids": [eid]})
    return rid, eid


async def test_submit_blocks_on_missing_receipt(realdb):
    # Manager creates the receipt-required policy; clerk builds + submits.
    async with realdb.client(key="a", role="ap_manager") as c:
        await _make_policy(c, name="ReceiptReq", category="travel", requires_receipt_above="10.00")
    async with realdb.client(key="a", role="ap_clerk") as c:
        rid, eid = await _make_report_with_expense(c, amount="50.00", receipt=False)
        resp = await c.post(f"/api/expense-reports/{rid}/submit")
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    codes = [v["code"] for v in detail["violations"]]
    assert "receipt_required" in codes

    # Report did NOT transition.
    async with realdb.client(key="a", role="ap_clerk") as c:
        report = (await c.get(f"/api/expense-reports/{rid}")).json()
    assert report["status"] == "draft"


async def test_submit_succeeds_when_clean(realdb):
    mk = realdb.sessionmaker("a")
    async with realdb.client(key="a", role="ap_clerk") as c:
        rid, eid = await _make_report_with_expense(c, amount="50.00", receipt=True)
        resp = await c.post(f"/api/expense-reports/{rid}/submit")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "submitted"
    assert body["submitted_at"]
    assert body["expenses"][0]["status"] == "submitted"

    async with mk() as s:
        actions = (
            (
                await s.execute(
                    select(AuditLog.action).where(AuditLog.entity_type == "expense_report")
                )
            )
            .scalars()
            .all()
        )
        assert "expense_report.submitted" in actions


async def test_submit_invalid_state_422(realdb):
    async with realdb.client(key="a", role="ap_clerk") as c:
        rid, _ = await _make_report_with_expense(c, amount="10.00", receipt=True)
        first = await c.post(f"/api/expense-reports/{rid}/submit")
        assert first.status_code == 200
        again = await c.post(f"/api/expense-reports/{rid}/submit")
    assert again.status_code == 422


# ---------------------------------------------------------------------------
# Report approve — segregation + CFO threshold
# ---------------------------------------------------------------------------


async def test_approve_self_blocked_by_segregation(realdb):
    # ap_manager builds + submits (so employee_user_id = manager), then the same
    # manager tries to approve → 403 (SoD).
    async with realdb.client(key="a", role="ap_manager") as c:
        rid, _ = await _make_report_with_expense(c, amount="100.00", receipt=True)
        await c.post(f"/api/expense-reports/{rid}/submit")
        resp = await c.post(f"/api/expense-reports/{rid}/approve")
    assert resp.status_code == 403
    assert "segregation" in resp.json()["detail"].lower()


async def test_different_manager_approves(realdb):
    mk = realdb.sessionmaker("a")
    # clerk submits; a different manager approves.
    async with realdb.client(key="a", role="ap_clerk") as c:
        rid, _ = await _make_report_with_expense(c, amount="100.00", receipt=True)
        await c.post(f"/api/expense-reports/{rid}/submit")
    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(f"/api/expense-reports/{rid}/approve")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "approved"
    assert body["approved_by"] == str(realdb.info("a").users["ap_manager"])
    assert body["expenses"][0]["status"] == "approved"

    async with mk() as s:
        actions = (
            (
                await s.execute(
                    select(AuditLog.action).where(AuditLog.entity_type == "expense_report")
                )
            )
            .scalars()
            .all()
        )
        assert "expense_report.approved" in actions


async def test_cfo_threshold_default_gates_manager(realdb):
    # A >5000 report can't be approved by ap_manager (default threshold), but a
    # CFO can.
    async with realdb.client(key="a", role="ap_clerk") as c:
        rid, _ = await _make_report_with_expense(c, amount="6000.00", receipt=True)
        await c.post(f"/api/expense-reports/{rid}/submit")
    async with realdb.client(key="a", role="ap_manager") as c:
        denied = await c.post(f"/api/expense-reports/{rid}/approve")
    assert denied.status_code == 403
    assert "cfo" in denied.json()["detail"].lower()

    async with realdb.client(key="a", role="cfo") as c:
        ok = await c.post(f"/api/expense-reports/{rid}/approve")
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "approved"


async def test_cfo_threshold_custom_override(realdb):
    # Lower the org threshold to 100 via the control DB; a 200 report then needs
    # a CFO even though it's well under the 5000 default.
    org_id = realdb.info("a").org_id
    ctrl = realdb.control_sessionmaker()
    from sqlalchemy.orm.attributes import flag_modified

    async with ctrl() as s:
        org = (await s.execute(select(Organization).where(Organization.id == org_id))).scalar_one()
        settings_dict = dict(org.settings or {})
        settings_dict["expense_approval"] = {"cfo_threshold": "100"}
        org.settings = settings_dict
        flag_modified(org, "settings")
        await s.commit()

    try:
        async with realdb.client(key="a", role="ap_clerk") as c:
            rid, _ = await _make_report_with_expense(c, amount="200.00", receipt=True)
            await c.post(f"/api/expense-reports/{rid}/submit")
        async with realdb.client(key="a", role="ap_manager") as c:
            denied = await c.post(f"/api/expense-reports/{rid}/approve")
        assert denied.status_code == 403
    finally:
        # Restore so other tests aren't affected (control DB isn't truncated).
        async with ctrl() as s:
            org = (
                await s.execute(select(Organization).where(Organization.id == org_id))
            ).scalar_one()
            settings_dict = dict(org.settings or {})
            settings_dict.pop("expense_approval", None)
            org.settings = settings_dict
            flag_modified(org, "settings")
            await s.commit()


# ---------------------------------------------------------------------------
# Report reject
# ---------------------------------------------------------------------------


async def test_reject_returns_children_to_draft(realdb):
    mk = realdb.sessionmaker("a")
    async with realdb.client(key="a", role="ap_clerk") as c:
        rid, eid = await _make_report_with_expense(c, amount="100.00", receipt=True)
        await c.post(f"/api/expense-reports/{rid}/submit")
    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(f"/api/expense-reports/{rid}/reject", json={"reason": "wrong GL"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "rejected"
    assert body["expenses"][0]["status"] == "draft"

    async with mk() as s:
        e = (await s.execute(select(Expense).where(Expense.id == uuid.UUID(eid)))).scalar_one()
        assert str(e.status) == "draft"
        actions = (
            (
                await s.execute(
                    select(AuditLog.action).where(AuditLog.action == "expense_report.rejected")
                )
            )
            .scalars()
            .all()
        )
        assert len(actions) >= 1


async def test_approve_invalid_state_422(realdb):
    # A draft report (never submitted) can't be approved.
    async with realdb.client(key="a", role="ap_clerk") as c:
        rid, _ = await _make_report_with_expense(c, amount="10.00", receipt=True)
    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(f"/api/expense-reports/{rid}/approve")
    assert resp.status_code == 422


async def test_policy_money_exact_numeric(realdb):
    mk = realdb.sessionmaker("a")
    async with realdb.client(key="a", role="ap_manager") as c:
        pid = (await _make_policy(c, name="Exact", category_limit="123.45", mileage_rate="0.6750"))[
            "id"
        ]
    async with mk() as s:
        p = (
            await s.execute(select(ExpensePolicy).where(ExpensePolicy.id == uuid.UUID(pid)))
        ).scalar_one()
        assert p.category_limit == Decimal("123.45")
        assert p.mileage_rate == Decimal("0.6750")


# ---------------------------------------------------------------------------
# Post-draft composition/amount lock (issue #155)
# ---------------------------------------------------------------------------


async def _submit_and_approve(realdb, amount="100.00"):
    """Clerk builds + submits a report; a different manager approves it.
    Returns (rid, eid)."""
    async with realdb.client(key="a", role="ap_clerk") as c:
        rid, eid = await _make_report_with_expense(c, amount=amount, receipt=True)
        assert (await c.post(f"/api/expense-reports/{rid}/submit")).status_code == 200
    async with realdb.client(key="a", role="ap_manager") as c:
        assert (await c.post(f"/api/expense-reports/{rid}/approve")).status_code == 200
    return rid, eid


async def test_cannot_attach_to_approved_report(realdb):
    rid, _ = await _submit_and_approve(realdb)
    async with realdb.client(key="a", role="ap_clerk") as c:
        new_eid = (
            await c.post("/api/expenses", json={"expense_date": "2026-06-02", "amount": "50000.00"})
        ).json()["id"]
        resp = await c.post(f"/api/expense-reports/{rid}/expenses", json={"expense_ids": [new_eid]})
        assert resp.status_code == 409, resp.text
        # Total unchanged — the $50k line never landed.
        report = (await c.get(f"/api/expense-reports/{rid}")).json()
    assert report["status"] == "approved"
    assert report["total_amount"] == 100.0


async def test_cannot_edit_amount_of_expense_on_approved_report(realdb):
    rid, eid = await _submit_and_approve(realdb)
    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await c.patch(f"/api/expenses/{eid}", json={"amount": "1.00"})
        assert resp.status_code == 409, resp.text
        report = (await c.get(f"/api/expense-reports/{rid}")).json()
    assert report["total_amount"] == 100.0


async def test_cannot_delete_expense_on_approved_report(realdb):
    rid, eid = await _submit_and_approve(realdb)
    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await c.delete(f"/api/expenses/{eid}")
        assert resp.status_code == 409, resp.text
        report = (await c.get(f"/api/expense-reports/{rid}")).json()
    assert report["total_amount"] == 100.0


async def test_cannot_detach_expense_from_submitted_report(realdb):
    async with realdb.client(key="a", role="ap_clerk") as c:
        rid, eid = await _make_report_with_expense(c, amount="100.00", receipt=True)
        assert (await c.post(f"/api/expense-reports/{rid}/submit")).status_code == 200
        # Detaching would shrink the submitted report's total below what approval
        # will see.
        resp = await c.post(
            f"/api/expense-reports/{rid}/expenses",
            json={"expense_ids": [eid], "detach": True},
        )
        assert resp.status_code == 409, resp.text


async def test_cannot_update_submitted_report_fields(realdb):
    async with realdb.client(key="a", role="ap_clerk") as c:
        rid, _ = await _make_report_with_expense(c, amount="100.00", receipt=True)
        assert (await c.post(f"/api/expense-reports/{rid}/submit")).status_code == 200
        resp = await c.patch(f"/api/expense-reports/{rid}", json={"currency": "EUR"})
        assert resp.status_code == 409, resp.text


async def test_rejected_report_expenses_can_be_re_reported(realdb):
    """A rejected report is terminal but its expenses drop back to draft and must
    remain movable onto a fresh draft report (the lock only bites locked states)."""
    async with realdb.client(key="a", role="ap_clerk") as c:
        rid, eid = await _make_report_with_expense(c, amount="100.00", receipt=True)
        assert (await c.post(f"/api/expense-reports/{rid}/submit")).status_code == 200
    async with realdb.client(key="a", role="ap_manager") as c:
        assert (await c.post(f"/api/expense-reports/{rid}/reject")).status_code == 200
    # Re-report onto a new draft report — moving off the rejected source is OK.
    async with realdb.client(key="a", role="ap_clerk") as c:
        new_rid = (
            await c.post(
                "/api/expense-reports", json={"report_number": f"R-{uuid.uuid4().hex[:8]}"}
            )
        ).json()["id"]
        resp = await c.post(f"/api/expense-reports/{new_rid}/expenses", json={"expense_ids": [eid]})
        assert resp.status_code == 200, resp.text
        moved = (await c.get(f"/api/expense-reports/{new_rid}")).json()
    assert moved["total_amount"] == 100.0
