"""Real-DB coverage for the corporate-card-transaction router (Expense
Management WF4) — ``backend/app/api/expense_cards.py``.

Covers CSV import + dedupe-skip, virtual-card sync + dedupe, match-suggestion
amount/date windowing, match/unmatch round-trip with both-sides linkage +
``payment_method``, create-expense-from-card, ignore, already-matched
rejection, RBAC denial, and tenant isolation. Money round-trips exact through
``Numeric(15, 2)``; audit rows are asserted on the trail.
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select

from app.models.entity import Entity
from app.models.expense import CorporateCardTransaction, Expense
from app.models.virtual_card import VirtualCard
from app.models.workflow import AuditLog


async def _default_entity_id(session) -> uuid.UUID:
    return (await session.execute(select(Entity.id).where(Entity.is_default))).scalar_one()


def _csv(*lines: str) -> bytes:
    return ("\n".join(lines) + "\n").encode("utf-8")


_HEADER = "external_txn_id,date,posted_date,merchant,amount,currency,card_last_four,card_ref"


# ---------------------------------------------------------------------------
# CSV import + dedupe
# ---------------------------------------------------------------------------


async def test_import_csv_creates_rows(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    csv_bytes = _csv(
        _HEADER,
        "ext-1,2026-06-01,2026-06-02,Uber,42.50,USD,1234,corp-card-a",
        "ext-2,2026-06-03,,Delta,500.00,USD,1234,corp-card-a",
    )
    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(
            "/api/corporate-card-transactions/import-csv",
            files={"file": ("cards.csv", csv_bytes, "text/csv")},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["imported"] == 2
    assert body["skipped"] == 0

    async with mk() as s:
        rows = (await s.execute(select(CorporateCardTransaction))).scalars().all()
        assert len(rows) == 2
        by_ext = {r.external_txn_id: r for r in rows}
        assert by_ext["ext-1"].amount == Decimal("42.50")  # exact Numeric round-trip
        assert by_ext["ext-1"].card_last_four == "1234"
        assert by_ext["ext-1"].reconciliation_status == "unmatched"
        # All rows in one upload share an import_batch stamp.
        assert by_ext["ext-1"].import_batch == by_ext["ext-2"].import_batch
        assert by_ext["ext-1"].organization_id == org_id


async def test_import_csv_dedupes_on_reimport(realdb):
    mk = realdb.sessionmaker("a")
    csv_bytes = _csv(
        _HEADER,
        "dup-1,2026-06-01,,Uber,10.00,USD,1234,c",
        "dup-2,2026-06-02,,Lyft,20.00,USD,1234,c",
    )
    async with realdb.client(key="a", role="ap_manager") as c:
        first = await c.post(
            "/api/corporate-card-transactions/import-csv",
            files={"file": ("cards.csv", csv_bytes, "text/csv")},
        )
        assert first.json()["imported"] == 2
        # Re-import the same file — every row is a dedupe skip.
        second = await c.post(
            "/api/corporate-card-transactions/import-csv",
            files={"file": ("cards.csv", csv_bytes, "text/csv")},
        )
    body = second.json()
    assert body["imported"] == 0
    assert body["skipped"] == 2

    async with mk() as s:
        rows = (await s.execute(select(CorporateCardTransaction))).scalars().all()
        assert len(rows) == 2  # no duplicate rows landed


async def test_import_csv_dedupes_within_one_file(realdb):
    mk = realdb.sessionmaker("a")
    csv_bytes = _csv(
        _HEADER,
        "same,2026-06-01,,Uber,10.00,USD,1234,c",
        "same,2026-06-01,,Uber,10.00,USD,1234,c",
    )
    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(
            "/api/corporate-card-transactions/import-csv",
            files={"file": ("cards.csv", csv_bytes, "text/csv")},
        )
    body = resp.json()
    assert body["imported"] == 1
    assert body["skipped"] == 1
    async with mk() as s:
        rows = (await s.execute(select(CorporateCardTransaction))).scalars().all()
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# Virtual-card sync
# ---------------------------------------------------------------------------


async def _seed_invoice_id(session, org_id):
    from app.models.invoice import Invoice

    inv = Invoice(
        organization_id=org_id,
        invoice_number=f"INV-{uuid.uuid4().hex[:8]}",
        vendor_name="Acme",
        amount=Decimal("1.00"),
    )
    session.add(inv)
    await session.flush()
    return inv.id


async def test_sync_virtual_cards_creates_and_dedupes(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    async with mk() as s:
        ent = await _default_entity_id(s)
        inv_id = await _seed_invoice_id(s, org_id)
        for n, amt in (("pc1", "120.00"), ("pc2", "75.50")):
            s.add(
                VirtualCard(
                    organization_id=org_id,
                    entity_id=ent,
                    invoice_id=inv_id,
                    card_provider="mock",
                    provider_card_id=n,
                    amount_limit=Decimal("500.00"),
                    amount_charged=Decimal(amt),
                    charged_at=datetime(2026, 6, 5, tzinfo=UTC),
                    merchant_name="AWS",
                    last_four="9999",
                    status="charged",
                    currency="USD",
                )
            )
        # A non-charged card must NOT be synced.
        s.add(
            VirtualCard(
                organization_id=org_id,
                entity_id=ent,
                invoice_id=inv_id,
                card_provider="mock",
                provider_card_id="pc3",
                amount_limit=Decimal("500.00"),
                status="created",
            )
        )
        await s.commit()

    async with realdb.client(key="a", role="ap_manager") as c:
        first = await c.post("/api/corporate-card-transactions/sync-virtual-cards")
        assert first.status_code == 200, first.text
        assert first.json() == {"created": 2, "skipped": 0}
        # Re-run is idempotent.
        second = await c.post("/api/corporate-card-transactions/sync-virtual-cards")
        assert second.json() == {"created": 0, "skipped": 2}

    async with mk() as s:
        rows = (await s.execute(select(CorporateCardTransaction))).scalars().all()
        assert len(rows) == 2
        by_ext = {r.external_txn_id: r for r in rows}
        assert "vc:pc1" in by_ext and "vc:pc2" in by_ext
        assert by_ext["vc:pc1"].amount == Decimal("120.00")
        assert by_ext["vc:pc1"].virtual_card_id is not None
        assert by_ext["vc:pc1"].txn_date == date(2026, 6, 5)
        assert by_ext["vc:pc1"].merchant == "AWS"


# ---------------------------------------------------------------------------
# Match suggestions
# ---------------------------------------------------------------------------


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


async def _make_expense(realdb, *, amount="42.50", expense_date="2026-06-01", merchant="Uber"):
    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await c.post(
            "/api/expenses",
            json={
                "expense_date": expense_date,
                "merchant": merchant,
                "amount": amount,
                "currency": "USD",
            },
        )
    return resp.json()["id"]


async def test_match_suggestions_amount_and_date_window(realdb):
    txn_id = await _make_txn(realdb, amount="42.50", txn_date="2026-06-10")
    in_window = await _make_expense(realdb, amount="42.50", expense_date="2026-06-12")
    # Same amount but outside the ±5d window — excluded.
    await _make_expense(realdb, amount="42.50", expense_date="2026-06-30")
    # In window but wrong amount — excluded.
    await _make_expense(realdb, amount="99.00", expense_date="2026-06-10")

    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await c.get(f"/api/corporate-card-transactions/{txn_id}/match-suggestions")
    assert resp.status_code == 200, resp.text
    suggestions = resp.json()
    ids = [s["expense"]["id"] for s in suggestions]
    assert in_window in ids
    assert len(ids) == 1


# ---------------------------------------------------------------------------
# Match / unmatch round-trip + both-sides linkage + payment_method
# ---------------------------------------------------------------------------


async def test_match_unmatch_roundtrip(realdb):
    mk = realdb.sessionmaker("a")
    txn_id = await _make_txn(realdb)
    expense_id = await _make_expense(realdb)

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(
            f"/api/corporate-card-transactions/{txn_id}/match",
            json={"expense_id": expense_id},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["reconciliation_status"] == "matched"
        assert resp.json()["matched_expense_id"] == expense_id

    async with mk() as s:
        txn = (
            await s.execute(
                select(CorporateCardTransaction).where(
                    CorporateCardTransaction.id == uuid.UUID(txn_id)
                )
            )
        ).scalar_one()
        exp = (
            await s.execute(select(Expense).where(Expense.id == uuid.UUID(expense_id)))
        ).scalar_one()
        assert str(txn.matched_expense_id) == expense_id
        assert str(exp.card_transaction_id) == txn_id
        assert exp.payment_method == "corporate_card"  # no virtual_card_id on txn
        actions = (
            await s.execute(
                select(AuditLog.action).where(
                    AuditLog.entity_type == "corporate_card_transaction"
                )
            )
        ).scalars().all()
        assert "card_txn.matched" in actions

    # Unmatch — both sides cleared.
    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(f"/api/corporate-card-transactions/{txn_id}/unmatch")
        assert resp.status_code == 200, resp.text
        assert resp.json()["reconciliation_status"] == "unmatched"
        assert resp.json()["matched_expense_id"] is None

    async with mk() as s:
        exp = (
            await s.execute(select(Expense).where(Expense.id == uuid.UUID(expense_id)))
        ).scalar_one()
        assert exp.card_transaction_id is None


async def test_match_virtual_card_sets_virtual_payment_method(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    async with mk() as s:
        ent = await _default_entity_id(s)
        inv_id = await _seed_invoice_id(s, org_id)
        s.add(
            VirtualCard(
                organization_id=org_id,
                entity_id=ent,
                invoice_id=inv_id,
                card_provider="mock",
                provider_card_id="pcvm",
                amount_limit=Decimal("500.00"),
                amount_charged=Decimal("60.00"),
                charged_at=datetime(2026, 6, 1, tzinfo=UTC),
                merchant_name="Slack",
                status="charged",
            )
        )
        await s.commit()
    async with realdb.client(key="a", role="ap_manager") as c:
        await c.post("/api/corporate-card-transactions/sync-virtual-cards")
        txn_id = (await c.get("/api/corporate-card-transactions")).json()["items"][0]["id"]
    expense_id = await _make_expense(realdb, amount="60.00", expense_date="2026-06-01")
    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(
            f"/api/corporate-card-transactions/{txn_id}/match",
            json={"expense_id": expense_id},
        )
        assert resp.status_code == 200, resp.text
    async with mk() as s:
        exp = (
            await s.execute(select(Expense).where(Expense.id == uuid.UUID(expense_id)))
        ).scalar_one()
        assert exp.payment_method == "virtual_card"


async def test_match_already_matched_rejected(realdb):
    txn_id = await _make_txn(realdb)
    e1 = await _make_expense(realdb)
    e2 = await _make_expense(realdb)
    async with realdb.client(key="a", role="ap_manager") as c:
        ok = await c.post(
            f"/api/corporate-card-transactions/{txn_id}/match", json={"expense_id": e1}
        )
        assert ok.status_code == 200
        dup = await c.post(
            f"/api/corporate-card-transactions/{txn_id}/match", json={"expense_id": e2}
        )
    assert dup.status_code == 409


async def test_unmatch_on_unmatched_rejected(realdb):
    # Unmatching a never-matched txn is a 409 (no spurious audit row).
    mk = realdb.sessionmaker("a")
    txn_id = await _make_txn(realdb)
    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(f"/api/corporate-card-transactions/{txn_id}/unmatch")
    assert resp.status_code == 409

    async with mk() as s:
        actions = (
            await s.execute(
                select(AuditLog.action).where(
                    AuditLog.entity_type == "corporate_card_transaction"
                )
            )
        ).scalars().all()
        assert "card_txn.unmatched" not in actions


# ---------------------------------------------------------------------------
# create-expense + ignore
# ---------------------------------------------------------------------------


async def test_create_expense_from_card(realdb):
    mk = realdb.sessionmaker("a")
    txn_id = await _make_txn(realdb, merchant="Hilton", amount="310.00", txn_date="2026-06-04")
    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await c.post(f"/api/corporate-card-transactions/{txn_id}/create-expense")
    assert resp.status_code == 200, resp.text
    assert resp.json()["reconciliation_status"] == "matched"

    async with mk() as s:
        exp = (await s.execute(select(Expense))).scalar_one()
        assert exp.amount == Decimal("310.00")
        assert exp.merchant == "Hilton"
        assert exp.expense_date == date(2026, 6, 4)
        assert exp.payment_method == "corporate_card"
        assert str(exp.card_transaction_id) == txn_id


async def test_ignore_card_transaction(realdb):
    txn_id = await _make_txn(realdb)
    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(f"/api/corporate-card-transactions/{txn_id}/ignore")
    assert resp.status_code == 200, resp.text
    assert resp.json()["reconciliation_status"] == "ignored"


# ---------------------------------------------------------------------------
# List filters
# ---------------------------------------------------------------------------


async def test_list_filters_by_status(realdb):
    txn_id = await _make_txn(realdb)
    await _make_txn(realdb, merchant="Other", amount="5.00", txn_date="2026-06-02")
    async with realdb.client(key="a", role="ap_manager") as c:
        await c.post(f"/api/corporate-card-transactions/{txn_id}/ignore")
        ignored = (
            await c.get("/api/corporate-card-transactions?reconciliation_status=ignored")
        ).json()
        unmatched = (
            await c.get("/api/corporate-card-transactions?reconciliation_status=unmatched")
        ).json()
    assert ignored["total"] == 1
    assert unmatched["total"] == 1


# ---------------------------------------------------------------------------
# RBAC + tenant isolation
# ---------------------------------------------------------------------------


async def test_cfo_cannot_mutate(realdb):
    txn_id = await _make_txn(realdb)
    async with realdb.client(key="a", role="cfo") as c:
        # CFO can read.
        assert (await c.get("/api/corporate-card-transactions")).status_code == 200
        # But not import / sync / ignore.
        imp = await c.post(
            "/api/corporate-card-transactions/import-csv",
            files={"file": ("cards.csv", _csv(_HEADER), "text/csv")},
        )
        assert imp.status_code == 403
        assert (
            await c.post("/api/corporate-card-transactions/sync-virtual-cards")
        ).status_code == 403
        assert (
            await c.post(f"/api/corporate-card-transactions/{txn_id}/ignore")
        ).status_code == 403


async def test_tenant_isolation(realdb):
    txn_id = await _make_txn(realdb)
    async with realdb.client(key="b", role="ap_manager") as c:
        assert (
            await c.get(f"/api/corporate-card-transactions/{txn_id}/match-suggestions")
        ).status_code == 404
        assert (
            await c.post(f"/api/corporate-card-transactions/{txn_id}/ignore")
        ).status_code == 404
