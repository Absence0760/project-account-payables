"""Corporate-card unmatch restores the pre-match payment method, and a
card-derived expense is policy-evaluated on creation.

Two defects in ``app/api/expense_cards.py``, both from the round-15 bug hunt:

1. ``POST /{id}/unmatch`` cleared both legs of the circular FK but left
   ``Expense.payment_method`` at whatever ``_link_both_sides`` stamped, so an
   expense reconciled against the wrong card line read ``corporate_card`` /
   ``virtual_card`` forever. Resetting to ``out_of_pocket`` would be a
   *different* wrong guess — an employee can legitimately mark an expense
   card-funded before its feed row is imported — so the fix records the
   pre-match value (``expenses.payment_method_before_match``, migration 0089)
   and puts THAT back. A row whose match predates the column has no recorded
   value: unmatch then leaves ``payment_method`` alone rather than inventing one.

2. ``POST /{id}/create-expense`` minted an expense without calling
   ``_refresh_policy_violations``, so a card-derived line carried no policy
   flags until something else happened to PATCH it — on the one kind of row
   that is, by construction, already spent money.
"""

import uuid

from sqlalchemy import select

from app.models.expense import Expense
from app.models.workflow import AuditLog

_HEADER = "external_txn_id,date,posted_date,merchant,amount,currency,card_last_four,card_ref"


def _csv(*lines: str) -> bytes:
    return ("\n".join(lines) + "\n").encode("utf-8")


async def _make_txn(realdb, *, merchant="Uber", amount="42.50", txn_date="2026-06-01"):
    csv_bytes = _csv(
        _HEADER,
        f"t-{uuid.uuid4().hex[:8]},{txn_date},,{merchant},{amount},USD,1234,c",
    )
    async with realdb.client(key="a", role="ap_manager") as c:
        await c.post(
            "/api/corporate-card-transactions/import-csv",
            files={"file": ("cards.csv", csv_bytes, "text/csv")},
        )
        rows = (await c.get("/api/corporate-card-transactions")).json()["items"]
    return rows[0]["id"]


async def _make_expense(realdb, *, amount="42.50", expense_date="2026-06-01", payment_method=None):
    body = {
        "expense_date": expense_date,
        "merchant": "Uber",
        "amount": amount,
        "currency": "USD",
    }
    if payment_method is not None:
        body["payment_method"] = payment_method
    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await c.post("/api/expenses", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _load_expense(mk, expense_id: str) -> Expense:
    async with mk() as s:
        return (
            await s.execute(select(Expense).where(Expense.id == uuid.UUID(expense_id)))
        ).scalar_one()


# ---------------------------------------------------------------------------
# 1. Unmatch restores what the match overwrote
# ---------------------------------------------------------------------------


async def test_unmatch_restores_out_of_pocket_payment_method(realdb):
    """The headline bug: an out-of-pocket expense mis-matched to a card line
    stayed ``corporate_card`` after the link was cleared."""
    mk = realdb.sessionmaker("a")
    txn_id = await _make_txn(realdb)
    expense_id = await _make_expense(realdb)

    async with realdb.client(key="a", role="ap_manager") as c:
        matched = await c.post(
            f"/api/corporate-card-transactions/{txn_id}/match",
            json={"expense_id": expense_id},
        )
        assert matched.status_code == 200, matched.text

    exp = await _load_expense(mk, expense_id)
    assert exp.payment_method == "corporate_card"  # the match stamped it
    assert exp.payment_method_before_match == "out_of_pocket"  # ...and recorded the original

    async with realdb.client(key="a", role="ap_manager") as c:
        unmatched = await c.post(f"/api/corporate-card-transactions/{txn_id}/unmatch")
        assert unmatched.status_code == 200, unmatched.text

    exp = await _load_expense(mk, expense_id)
    assert exp.card_transaction_id is None
    # Pre-fix this was still "corporate_card".
    assert exp.payment_method == "out_of_pocket"
    # The record is consumed — a non-NULL value means "currently matched".
    assert exp.payment_method_before_match is None

    # The restore is on the trail, PII-free (an enum value, not who spent what).
    async with mk() as s:
        details = (
            (
                await s.execute(
                    select(AuditLog.details).where(AuditLog.action == "expense.card_unmatched")
                )
            )
            .scalars()
            .all()
        )
    assert len(details) == 1
    assert details[0]["payment_method_restored"] == "out_of_pocket"


async def test_unmatch_restores_a_pre_existing_card_marking(realdb):
    """An expense the employee already marked card-funded before the feed row
    landed keeps ITS OWN marking, not the one the match stamped over it.

    ``virtual_card`` in → matched to a plain corporate-card txn (stamps
    ``corporate_card``) → unmatch must give ``virtual_card`` back. Pre-fix it
    kept ``corporate_card``; a blanket reset to ``out_of_pocket`` would be just
    as wrong."""
    mk = realdb.sessionmaker("a")
    txn_id = await _make_txn(realdb)
    expense_id = await _make_expense(realdb, payment_method="virtual_card")

    async with realdb.client(key="a", role="ap_manager") as c:
        matched = await c.post(
            f"/api/corporate-card-transactions/{txn_id}/match",
            json={"expense_id": expense_id},
        )
        assert matched.status_code == 200, matched.text

    exp = await _load_expense(mk, expense_id)
    assert exp.payment_method == "corporate_card"  # the txn carries no virtual_card_id

    async with realdb.client(key="a", role="ap_manager") as c:
        await c.post(f"/api/corporate-card-transactions/{txn_id}/unmatch")

    exp = await _load_expense(mk, expense_id)
    assert exp.payment_method == "virtual_card"
    assert exp.payment_method_before_match is None


async def test_unmatch_leaves_payment_method_alone_when_no_pre_match_value(realdb):
    """A row matched BEFORE migration 0089 has no recorded pre-match value.

    Nothing in the schema can recover it — ``payment_method`` already holds the
    match's own stamp — so unmatch must leave it exactly as it is. Writing
    ``out_of_pocket`` there would manufacture the guess the column exists to
    avoid; this asserts the no-guess behaviour explicitly."""
    mk = realdb.sessionmaker("a")
    txn_id = await _make_txn(realdb)
    expense_id = await _make_expense(realdb)

    async with realdb.client(key="a", role="ap_manager") as c:
        await c.post(
            f"/api/corporate-card-transactions/{txn_id}/match",
            json={"expense_id": expense_id},
        )

    # Simulate the legacy shape: matched, with nothing recorded.
    async with mk() as s:
        legacy = (
            await s.execute(select(Expense).where(Expense.id == uuid.UUID(expense_id)))
        ).scalar_one()
        legacy.payment_method_before_match = None
        await s.commit()

    async with realdb.client(key="a", role="ap_manager") as c:
        unmatched = await c.post(f"/api/corporate-card-transactions/{txn_id}/unmatch")
        assert unmatched.status_code == 200, unmatched.text

    exp = await _load_expense(mk, expense_id)
    assert exp.card_transaction_id is None  # the link IS cleared
    assert exp.payment_method == "corporate_card"  # ...but the method is untouched
    assert exp.payment_method_before_match is None

    async with mk() as s:
        details = (
            (
                await s.execute(
                    select(AuditLog.details).where(AuditLog.action == "expense.card_unmatched")
                )
            )
            .scalars()
            .all()
        )
    assert len(details) == 1
    assert details[0]["payment_method_restored"] is None


async def test_rematch_after_unmatch_records_the_restored_value(realdb):
    """The record/restore cycle is repeatable — the second match records what
    the first one put back, not the stamp it is about to write."""
    mk = realdb.sessionmaker("a")
    txn_id = await _make_txn(realdb)
    expense_id = await _make_expense(realdb)

    async with realdb.client(key="a", role="ap_manager") as c:
        await c.post(
            f"/api/corporate-card-transactions/{txn_id}/match",
            json={"expense_id": expense_id},
        )
        await c.post(f"/api/corporate-card-transactions/{txn_id}/unmatch")
        again = await c.post(
            f"/api/corporate-card-transactions/{txn_id}/match",
            json={"expense_id": expense_id},
        )
        assert again.status_code == 200, again.text

    exp = await _load_expense(mk, expense_id)
    assert exp.payment_method == "corporate_card"
    assert exp.payment_method_before_match == "out_of_pocket"


# ---------------------------------------------------------------------------
# 2. create-expense evaluates policy immediately
# ---------------------------------------------------------------------------


async def test_create_expense_from_card_carries_policy_violations(realdb):
    """A card-derived line is policy-evaluated on creation, like every other
    expense write path. Pre-fix ``policy_violations`` was ``None`` until the
    next unrelated PATCH."""
    mk = realdb.sessionmaker("a")
    async with realdb.client(key="a", role="ap_manager") as c:
        policy = await c.post(
            "/api/expense-policies",
            json={"name": "Receipts", "requires_receipt_above": "10.00"},
        )
        assert policy.status_code == 201, policy.text

    txn_id = await _make_txn(realdb, amount="42.50")

    async with realdb.client(key="a", role="ap_manager") as c:
        created = await c.post(f"/api/corporate-card-transactions/{txn_id}/create-expense")
        assert created.status_code == 200, created.text
        expense_id = created.json()["matched_expense_id"]

    exp = await _load_expense(mk, expense_id)
    assert exp.policy_violations, "card-derived expense carried no policy flags"
    codes = {v["code"] for v in exp.policy_violations}
    # 42.50 > 10.00 with no receipt attached — the blocking rule.
    assert "receipt_required" in codes


async def test_create_expense_from_card_leaves_clean_line_unflagged(realdb):
    """The refresh is a real evaluation, not a blanket stamp: a line under
    every threshold comes back with no violations."""
    mk = realdb.sessionmaker("a")
    async with realdb.client(key="a", role="ap_manager") as c:
        await c.post(
            "/api/expense-policies",
            json={"name": "Receipts", "requires_receipt_above": "500.00"},
        )

    txn_id = await _make_txn(realdb, amount="42.50")
    async with realdb.client(key="a", role="ap_manager") as c:
        created = await c.post(f"/api/corporate-card-transactions/{txn_id}/create-expense")
        expense_id = created.json()["matched_expense_id"]

    exp = await _load_expense(mk, expense_id)
    assert not exp.policy_violations
