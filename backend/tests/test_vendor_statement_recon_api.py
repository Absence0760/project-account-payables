"""Real-DB coverage for the vendor-statement reconciliation router
(``app/api/vendor_statement_recon.py``).

Exercises create-from-lines + CSV upload (with a 422 on a malformed CSV), the
list/filter, the detail-with-lines view, line resolution flipping a run to
``resolved``, the close-readiness materiality gate, delete-with-cascade, and
RBAC — end-to-end against the live test tenants. Reconciliation math is owned by
the (separately tested) ``services.vendor_statement_recon`` engine; here we
prove the HTTP surface wires it through correctly with exact ``Numeric`` money.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.models.entity import Entity
from app.models.invoice import Invoice, InvoiceStatus
from app.models.vendor import Vendor
from app.models.vendor_statement_recon import (
    VendorStatementReconciliation,
    VendorStatementReconLine,
)
from app.models.workflow import AuditLog

_TODAY = date.today()


async def _default_entity_id(s):
    return (
        await s.execute(select(Entity.id).where(Entity.is_default.is_(True)).limit(1))
    ).scalar_one()


async def _add_vendor(mk, org_id, name="Globex Industrial") -> str:
    async with mk() as s:
        v = Vendor(organization_id=org_id, name=name, entity_id=await _default_entity_id(s))
        s.add(v)
        await s.commit()
        await s.refresh(v)
        return str(v.id)


async def _add_invoice(
    mk,
    org_id,
    *,
    vendor_id,
    invoice_number,
    amount="1000.00",
    status=InvoiceStatus.approved,
    invoice_date=None,
) -> str:
    async with mk() as s:
        inv = Invoice(
            organization_id=org_id,
            entity_id=await _default_entity_id(s),
            invoice_number=invoice_number,
            vendor_name="Globex Industrial",
            vendor_id=uuid.UUID(vendor_id),
            amount=Decimal(amount),
            currency="USD",
            invoice_date=invoice_date or _TODAY,
            due_date=_TODAY + timedelta(days=30),
            status=status,
        )
        s.add(inv)
        await s.commit()
        await s.refresh(inv)
        return str(inv.id)


def _line(invoice_number, amount, *, invoice_date=None, status="open"):
    return {
        "invoice_number": invoice_number,
        "invoice_date": (invoice_date or _TODAY).isoformat(),
        "amount": amount,
        "status": status,
    }


# ---------------------------------------------------------------------------
# create from lines — classification
# ---------------------------------------------------------------------------


async def test_create_from_lines_classifies_and_audits(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vendor_id = await _add_vendor(mk, org_id)
    # Two of our open invoices.
    await _add_invoice(mk, org_id, vendor_id=vendor_id, invoice_number="INV-100", amount="1000.00")
    await _add_invoice(mk, org_id, vendor_id=vendor_id, invoice_number="INV-200", amount="2000.00")

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(
            "/api/vendor-statements",
            json={
                "vendor_id": vendor_id,
                "statement_date": _TODAY.isoformat(),
                "currency": "USD",
                "lines": [
                    _line("INV-100", "1000.00"),  # matched
                    _line("INV-999", "500.00"),  # missing on our side
                    # INV-200 omitted → missing on their side
                ],
            },
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()

    by_class = {}
    for ln in body["lines"]:
        by_class.setdefault(ln["classification"], []).append(ln)

    assert len(by_class["matched"]) == 1
    assert by_class["matched"][0]["statement_invoice_number"] == "INV-100"
    assert by_class["matched"][0]["matched_invoice_number"] == "INV-100"
    assert by_class["matched"][0]["statement_amount"] == 1000.0

    assert len(by_class["missing_on_our_side"]) == 1
    assert by_class["missing_on_our_side"][0]["statement_invoice_number"] == "INV-999"

    assert len(by_class["missing_on_their_side"]) == 1
    assert by_class["missing_on_their_side"][0]["matched_invoice_number"] == "INV-200"

    summary = body["summary"]
    assert summary["matched_count"] == 1
    assert summary["missing_our_side_count"] == 1
    assert summary["missing_their_side_count"] == 1
    assert body["status"] == "open"
    assert body["source_format"] == "manual"

    async with mk() as s:
        audit = (
            await s.execute(
                select(AuditLog).where(
                    AuditLog.action == "vendor_statement_recon.created",
                    AuditLog.entity_id == uuid.UUID(body["id"]),
                )
            )
        ).scalar_one()
        assert audit.entity_type == "vendor_statement_reconciliation"


async def test_create_amount_mismatch_line(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vendor_id = await _add_vendor(mk, org_id)
    await _add_invoice(mk, org_id, vendor_id=vendor_id, invoice_number="INV-300", amount="1000.00")

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(
            "/api/vendor-statements",
            json={
                "vendor_id": vendor_id,
                "statement_date": _TODAY.isoformat(),
                # Supplier claims 1100 vs our 1000 → mismatch of 100.
                "lines": [_line("INV-300", "1100.00")],
            },
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    line = body["lines"][0]
    assert line["classification"] == "amount_mismatch"
    assert line["statement_amount"] == 1100.0
    assert line["ledger_amount"] == 1000.0
    assert line["amount_difference"] == 100.0
    assert body["summary"]["amount_mismatch_count"] == 1


async def test_create_fully_matched_run_is_resolved_immediately(realdb):
    """Issue #185: a statement that reconciles cleanly (every line `matched`,
    nothing actionable) has no `resolve_line` call ahead of it — the run must
    get its final status at creation, not sit `open` forever."""
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vendor_id = await _add_vendor(mk, org_id)
    await _add_invoice(mk, org_id, vendor_id=vendor_id, invoice_number="CLEAN-1", amount="100.00")
    await _add_invoice(mk, org_id, vendor_id=vendor_id, invoice_number="CLEAN-2", amount="200.00")

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(
            "/api/vendor-statements",
            json={
                "vendor_id": vendor_id,
                "statement_date": _TODAY.isoformat(),
                "lines": [_line("CLEAN-1", "100.00"), _line("CLEAN-2", "200.00")],
            },
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert {ln["classification"] for ln in body["lines"]} == {"matched"}
    assert body["summary"]["matched_count"] == 2
    assert body["status"] == "resolved"

    # And it's actually persisted that way, not just in the response shape.
    async with mk() as s:
        run = (
            await s.execute(
                select(VendorStatementReconciliation).where(
                    VendorStatementReconciliation.id == uuid.UUID(body["id"])
                )
            )
        ).scalar_one()
        assert run.status == "resolved"


async def test_create_run_with_actionable_line_stays_open_until_resolved(realdb):
    """Regression: a run with at least one actionable (non-matched) line must
    still start `open`, and only flip to `resolved` once a human clears the
    last actionable line via `resolve_line` — the create-time recompute must
    not short-circuit that existing behavior."""
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vendor_id = await _add_vendor(mk, org_id)
    await _add_invoice(mk, org_id, vendor_id=vendor_id, invoice_number="MIX-1", amount="100.00")

    async with realdb.client(key="a", role="ap_manager") as c:
        create_resp = await c.post(
            "/api/vendor-statements",
            json={
                "vendor_id": vendor_id,
                "statement_date": _TODAY.isoformat(),
                # MIX-1 matches; MIX-2 has no corresponding invoice → actionable.
                "lines": [_line("MIX-1", "100.00"), _line("MIX-2", "50.00")],
            },
        )
        assert create_resp.status_code == 201, create_resp.text
        detail = create_resp.json()
        assert detail["status"] == "open"

        actionable = next(
            ln for ln in detail["lines"] if ln["classification"] == "missing_on_our_side"
        )
        resolved = (
            await c.post(
                f"/api/vendor-statements/{detail['id']}/lines/{actionable['id']}/resolve",
                json={"resolution_status": "resolved", "resolution_note": "created the invoice"},
            )
        ).json()
    assert resolved["status"] == "resolved"


async def test_create_404_unknown_vendor(realdb):
    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(
            "/api/vendor-statements",
            json={
                "vendor_id": str(uuid.uuid4()),
                "statement_date": _TODAY.isoformat(),
                "lines": [_line("INV-1", "10.00")],
            },
        )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# CSV upload
# ---------------------------------------------------------------------------


async def test_upload_csv_happy_path(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vendor_id = await _add_vendor(mk, org_id)
    await _add_invoice(mk, org_id, vendor_id=vendor_id, invoice_number="INV-500", amount="750.00")

    csv_body = (
        b"invoice_number,date,amount,status\r\n"
        b"INV-500,2026-06-01,750.00,open\r\n"
        b"INV-501,2026-06-02,250.00,open\r\n"
    )
    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(
            "/api/vendor-statements/upload",
            data={
                "vendor_id": vendor_id,
                "statement_date": _TODAY.isoformat(),
                "statement_reference": "STMT-Q2",
                "currency": "USD",
            },
            files={"file": ("statement.csv", csv_body, "text/csv")},
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["source_format"] == "csv"
    assert body["file_key"] is None  # raw-file storage deferred
    assert body["statement_reference"] == "STMT-Q2"
    classes = {ln["classification"] for ln in body["lines"]}
    assert "matched" in classes
    assert "missing_on_our_side" in classes


async def test_upload_csv_malformed_returns_422(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vendor_id = await _add_vendor(mk, org_id)

    # Header row only, no data rows → StatementParseError.
    bad = b"invoice_number,amount\r\n"
    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(
            "/api/vendor-statements/upload",
            data={"vendor_id": vendor_id, "statement_date": _TODAY.isoformat()},
            files={"file": ("bad.csv", bad, "text/csv")},
        )
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# list + filter
# ---------------------------------------------------------------------------


async def test_list_filters_by_vendor_and_status(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    v1 = await _add_vendor(mk, org_id, name="Vendor One")
    v2 = await _add_vendor(mk, org_id, name="Vendor Two")

    async with realdb.client(key="a", role="ap_manager") as c:
        r1 = (
            await c.post(
                "/api/vendor-statements",
                json={
                    "vendor_id": v1,
                    "statement_date": _TODAY.isoformat(),
                    "lines": [_line("A-1", "10.00")],
                },
            )
        ).json()
        await c.post(
            "/api/vendor-statements",
            json={
                "vendor_id": v2,
                "statement_date": _TODAY.isoformat(),
                "lines": [_line("B-1", "20.00")],
            },
        )

        only_v1 = (await c.get(f"/api/vendor-statements?vendor_id={v1}")).json()
        assert [i["id"] for i in only_v1["items"]] == [r1["id"]]
        assert only_v1["total"] == 1
        # List omits lines.
        assert only_v1["items"][0]["lines"] is None

        open_runs = (await c.get("/api/vendor-statements?status=open")).json()
        assert open_runs["total"] >= 2
        resolved = (await c.get("/api/vendor-statements?status=resolved")).json()
        assert resolved["total"] == 0


# ---------------------------------------------------------------------------
# detail
# ---------------------------------------------------------------------------


async def test_get_detail_includes_lines(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vendor_id = await _add_vendor(mk, org_id)
    await _add_invoice(mk, org_id, vendor_id=vendor_id, invoice_number="D-1", amount="100.00")

    async with realdb.client(key="a", role="ap_manager") as c:
        run_id = (
            await c.post(
                "/api/vendor-statements",
                json={
                    "vendor_id": vendor_id,
                    "statement_date": _TODAY.isoformat(),
                    "lines": [_line("D-1", "100.00")],
                },
            )
        ).json()["id"]
        # ap_clerk can read.
    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await c.get(f"/api/vendor-statements/{run_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["lines"] is not None
    assert body["lines"][0]["matched_invoice_number"] == "D-1"


async def test_get_detail_404_cross_tenant(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vendor_id = await _add_vendor(mk, org_id)
    async with realdb.client(key="a", role="ap_manager") as c:
        run_id = (
            await c.post(
                "/api/vendor-statements",
                json={
                    "vendor_id": vendor_id,
                    "statement_date": _TODAY.isoformat(),
                    "lines": [_line("X-1", "10.00")],
                },
            )
        ).json()["id"]
    async with realdb.client(key="b", role="ap_manager") as c:
        resp = await c.get(f"/api/vendor-statements/{run_id}")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# resolve a line → run flips to resolved
# ---------------------------------------------------------------------------


async def test_resolve_only_actionable_line_resolves_run(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vendor_id = await _add_vendor(mk, org_id)
    # One matched (non-actionable) + one missing-our-side (the only actionable).
    await _add_invoice(mk, org_id, vendor_id=vendor_id, invoice_number="R-1", amount="100.00")

    async with realdb.client(key="a", role="ap_manager") as c:
        detail = (
            await c.post(
                "/api/vendor-statements",
                json={
                    "vendor_id": vendor_id,
                    "statement_date": _TODAY.isoformat(),
                    "lines": [_line("R-1", "100.00"), _line("R-2", "50.00")],
                },
            )
        ).json()
        assert detail["status"] == "open"
        actionable = next(
            ln for ln in detail["lines"] if ln["classification"] == "missing_on_our_side"
        )

        resolved = (
            await c.post(
                f"/api/vendor-statements/{detail['id']}/lines/{actionable['id']}/resolve",
                json={"resolution_status": "resolved", "resolution_note": "invoice created"},
            )
        ).json()

    assert resolved["status"] == "resolved"
    flipped = next(ln for ln in resolved["lines"] if ln["id"] == actionable["id"])
    assert flipped["resolution_status"] == "resolved"
    assert flipped["resolution_note"] == "invoice created"
    assert flipped["resolved_at"] is not None

    async with mk() as s:
        audit = (
            await s.execute(
                select(AuditLog).where(
                    AuditLog.action == "vendor_statement_recon.line_resolved",
                    AuditLog.entity_id == uuid.UUID(detail["id"]),
                )
            )
        ).scalar_one()
        assert audit.entity_type == "vendor_statement_reconciliation"


async def test_resolve_back_to_unresolved_clears_and_reopens(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vendor_id = await _add_vendor(mk, org_id)

    async with realdb.client(key="a", role="ap_manager") as c:
        detail = (
            await c.post(
                "/api/vendor-statements",
                json={
                    "vendor_id": vendor_id,
                    "statement_date": _TODAY.isoformat(),
                    "lines": [_line("U-1", "75.00")],  # missing_on_our_side, actionable
                },
            )
        ).json()
        line_id = detail["lines"][0]["id"]
        # Resolve → run resolved.
        r1 = (
            await c.post(
                f"/api/vendor-statements/{detail['id']}/lines/{line_id}/resolve",
                json={"resolution_status": "ignored"},
            )
        ).json()
        assert r1["status"] == "resolved"
        # Back to unresolved → run open again + resolved_* cleared.
        r2 = (
            await c.post(
                f"/api/vendor-statements/{detail['id']}/lines/{line_id}/resolve",
                json={"resolution_status": "unresolved"},
            )
        ).json()
    assert r2["status"] == "open"
    assert r2["lines"][0]["resolution_status"] == "unresolved"
    assert r2["lines"][0]["resolved_at"] is None


# ---------------------------------------------------------------------------
# close-readiness
# ---------------------------------------------------------------------------


async def test_close_readiness_flags_over_threshold_clears_under(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vendor_id = await _add_vendor(mk, org_id, name="Big Balance Co")

    async with realdb.client(key="a", role="ap_manager") as c:
        # A 5000 missing-our-side line → unreconciled 5000.
        await c.post(
            "/api/vendor-statements",
            json={
                "vendor_id": vendor_id,
                "statement_date": _TODAY.isoformat(),
                "lines": [_line("BIG-1", "5000.00")],
            },
        )

        # Materiality 1000 → vendor is blocking.
        over = (await c.get("/api/vendor-statements/close-readiness?materiality=1000")).json()
        assert over["is_close_ready"] is False
        assert over["materiality_threshold"] == 1000.0
        blocked = [v for v in over["blocking_vendors"] if v["vendor_id"] == vendor_id]
        assert len(blocked) == 1
        assert blocked[0]["unreconciled_amount"] == 5000.0
        assert blocked[0]["missing_our_side_count"] == 1

        # Materiality 10000 → under threshold, not blocking.
        under = (await c.get("/api/vendor-statements/close-readiness?materiality=10000")).json()
        assert all(v["vendor_id"] != vendor_id for v in under["blocking_vendors"])


async def test_close_readiness_ignores_resolved_lines(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vendor_id = await _add_vendor(mk, org_id, name="Cleared Co")

    async with realdb.client(key="a", role="ap_manager") as c:
        detail = (
            await c.post(
                "/api/vendor-statements",
                json={
                    "vendor_id": vendor_id,
                    "statement_date": _TODAY.isoformat(),
                    "lines": [_line("CL-1", "5000.00")],
                },
            )
        ).json()
        line_id = detail["lines"][0]["id"]
        await c.post(
            f"/api/vendor-statements/{detail['id']}/lines/{line_id}/resolve",
            json={"resolution_status": "resolved"},
        )
        # The run flipped to resolved, so close-readiness (which only looks at
        # OPEN runs) no longer considers this vendor.
        res = (await c.get("/api/vendor-statements/close-readiness?materiality=1000")).json()
    assert all(v["vendor_id"] != vendor_id for v in res["blocking_vendors"])


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


async def test_delete_cascades_lines(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vendor_id = await _add_vendor(mk, org_id)

    async with realdb.client(key="a", role="ap_manager") as c:
        run_id = (
            await c.post(
                "/api/vendor-statements",
                json={
                    "vendor_id": vendor_id,
                    "statement_date": _TODAY.isoformat(),
                    "lines": [_line("DEL-1", "10.00"), _line("DEL-2", "20.00")],
                },
            )
        ).json()["id"]
        resp = await c.delete(f"/api/vendor-statements/{run_id}")
    assert resp.status_code == 204

    async with mk() as s:
        run = (
            await s.execute(
                select(VendorStatementReconciliation).where(
                    VendorStatementReconciliation.id == uuid.UUID(run_id)
                )
            )
        ).scalar_one_or_none()
        assert run is None
        remaining = (
            (
                await s.execute(
                    select(VendorStatementReconLine).where(
                        VendorStatementReconLine.reconciliation_id == uuid.UUID(run_id)
                    )
                )
            )
            .scalars()
            .all()
        )
        assert remaining == []


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------


async def test_clerk_cannot_create(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vendor_id = await _add_vendor(mk, org_id)
    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await c.post(
            "/api/vendor-statements",
            json={
                "vendor_id": vendor_id,
                "statement_date": _TODAY.isoformat(),
                "lines": [_line("RB-1", "10.00")],
            },
        )
    assert resp.status_code == 403
