"""Real-DB coverage for the US 1099 tax surface in app/api/tax.py.

Exercises the SQL aggregation path (``build_1099_dashboard`` /
``build_1099_report``) and the TIN-verify + e-file endpoints end-to-end
against a live test tenant. Skips automatically when no Postgres is
available (the ``realdb`` fixture handles that).
"""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select

from app.models.invoice import Invoice, InvoiceStatus
from app.models.payment import Payment
from app.models.tax_filing import Tax1099Filing
from app.models.vendor import Vendor

YEAR = 2026


async def _vendor(mk, org_id, *, name, eligible, tax_id="12-3456789", w9=False, tin_verified=False):
    async with mk() as s:
        v = Vendor(
            organization_id=org_id,
            name=name,
            tax_id=tax_id,
            is_1099_eligible=eligible,
            w9_file_key=("org/w9/x.pdf" if w9 else None),
            tin_verified_at=(datetime.now(UTC) if tin_verified else None),
        )
        s.add(v)
        await s.commit()
        await s.refresh(v)
        return v.id


async def _paid_invoice(mk, org_id, vendor_id, amount):
    async with mk() as s:
        inv = Invoice(
            organization_id=org_id,
            invoice_number=f"INV-{vendor_id}",
            vendor_name="x",
            amount=Decimal(amount),
            status=InvoiceStatus.paid,
            vendor_id=vendor_id,
        )
        s.add(inv)
        await s.commit()
        await s.refresh(inv)
        p = Payment(
            invoice_id=inv.id,
            amount=Decimal(amount),
            status="completed",
            completed_at=datetime(YEAR, 6, 1, tzinfo=UTC),
        )
        s.add(p)
        await s.commit()


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


async def test_dashboard_flags_threshold_and_readiness(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id

    # Eligible, over threshold, fully ready (W-9 + TIN verified).
    v_ready = await _vendor(mk, org_id, name="Ready Co", eligible=True, w9=True, tin_verified=True)
    await _paid_invoice(mk, org_id, v_ready, "5000.00")
    # Eligible, over threshold, missing W-9 + TIN → needs attention.
    v_gap = await _vendor(mk, org_id, name="Gap Co", eligible=True)
    await _paid_invoice(mk, org_id, v_gap, "1200.00")
    # Eligible, under threshold → not counted as over.
    v_small = await _vendor(mk, org_id, name="Small Co", eligible=True, w9=True)
    await _paid_invoice(mk, org_id, v_small, "100.00")

    async with realdb.client(key="a", role="cfo") as c:
        resp = await c.get(f"/api/tax/1099-dashboard?year={YEAR}")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["vendor_count_eligible"] == 3
    assert body["vendor_count_eligible_over_threshold"] == 2
    assert body["vendor_count_over_threshold_without_w9"] == 1
    assert body["vendor_count_over_threshold_tin_unverified"] == 1
    assert body["vendor_count_needs_attention"] == 1
    # Reportable total = 5000 + 1200 (small is under threshold).
    assert body["total_reportable_usd"] == "6200.00"

    rows = {r["vendor_name"]: r for r in body["rows"]}
    assert rows["Ready Co"]["needs_attention"] is False
    assert rows["Ready Co"]["tin_verified"] is True
    assert rows["Gap Co"]["needs_attention"] is True


async def test_zero_payment_vendor_has_clean_zero_ytd(realdb):
    """A vendor with no completed payments must report a clean Decimal $0 YTD —
    the coalesce fallback is Decimal('0'), not int 0 (which can promote the
    aggregate off Numeric and mis-classify a vendor right at the $600 line) —
    and must never trip the threshold."""
    from decimal import Decimal

    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    await _vendor(mk, org_id, name="No Pay Co", eligible=True, w9=True)

    async with realdb.client(key="a", role="cfo") as c:
        resp = await c.get(f"/api/tax/1099-dashboard?year={YEAR}")
    assert resp.status_code == 200, resp.text
    row = next(r for r in resp.json()["rows"] if r["vendor_name"] == "No Pay Co")
    assert Decimal(row["ytd_paid"]) == Decimal("0")
    assert row["over_threshold"] is False


# ---------------------------------------------------------------------------
# TIN verify
# ---------------------------------------------------------------------------


async def test_tin_verify_stamps_on_valid(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vid = await _vendor(mk, org_id, name="Verify Co", eligible=True, tax_id="12-3456789")

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(f"/api/tax/vendors/{vid}/tin-verify", json={})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tin_validation"]["verdict"] == "valid"
    assert body["tin_validation"]["tin_last4"] == "6789"
    # No raw TIN in the response body.
    assert "123456789" not in resp.text
    assert body["tin_verified_at"] is not None

    async with mk() as s:
        v = (await s.execute(select(Vendor).where(Vendor.id == vid))).scalar_one()
        assert v.tin_verified_at is not None


async def test_tin_verify_invalid_clears_stamp(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vid = await _vendor(
        mk, org_id, name="Bad Co", eligible=True, tax_id="12-3456789", tin_verified=True
    )

    async with realdb.client(key="a", role="ap_manager") as c:
        # Override with a malformed TIN — verdict invalid, stamp cleared.
        resp = await c.post(f"/api/tax/vendors/{vid}/tin-verify", json={"tax_id": "00-0000000"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tin_validation"]["verdict"] == "invalid"
    assert body["tin_verified_at"] is None


async def test_tin_verify_requires_manager_role(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vid = await _vendor(mk, org_id, name="RBAC Co", eligible=True)

    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await c.post(f"/api/tax/vendors/{vid}/tin-verify", json={})
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Form download
# ---------------------------------------------------------------------------


async def test_download_1099_nec_pdf(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vid = await _vendor(mk, org_id, name="Form Co", eligible=True, w9=True)
    await _paid_invoice(mk, org_id, vid, "2500.00")

    async with realdb.client(key="a", role="admin") as c:
        resp = await c.get(f"/api/tax/vendors/{vid}/1099?year={YEAR}&form_type=1099-NEC")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF")


async def test_download_1099_no_payments_400(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vid = await _vendor(mk, org_id, name="Empty Co", eligible=True)

    async with realdb.client(key="a", role="admin") as c:
        resp = await c.get(f"/api/tax/vendors/{vid}/1099?year={YEAR}")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# E-filing (idempotency)
# ---------------------------------------------------------------------------


async def test_file_1099_batch_is_idempotent(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    v = await _vendor(mk, org_id, name="File Co", eligible=True, w9=True)
    await _paid_invoice(mk, org_id, v, "3000.00")

    async with realdb.client(key="a", role="ap_manager") as c:
        r1 = await c.post("/api/tax/1099/file", json={"year": YEAR, "idempotency_key": "batch-1"})
        r2 = await c.post("/api/tax/1099/file", json={"year": YEAR, "idempotency_key": "batch-1"})

    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text
    b1, b2 = r1.json(), r2.json()
    assert b1["status"] == "accepted"
    assert b1["accepted_count"] == 1
    assert b1["already_filed"] is False
    # Second call returns the stored confirmation — no re-file.
    assert b2["already_filed"] is True
    assert b2["confirmation_number"] == b1["confirmation_number"]
    assert b2["filing_id"] == b1["filing_id"]

    # Exactly one filing row persisted for the key.
    async with mk() as s:
        rows = (
            (
                await s.execute(
                    select(Tax1099Filing).where(Tax1099Filing.idempotency_key == "batch-1")
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].result is not None
        # No TIN persisted in the filing result.
        assert "123456789" not in str(rows[0].result)


async def test_file_1099_only_over_threshold_eligible(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    # Eligible + over threshold → filed.
    big = await _vendor(mk, org_id, name="Big Co", eligible=True, w9=True)
    await _paid_invoice(mk, org_id, big, "5000.00")
    # Eligible but under threshold → excluded.
    small = await _vendor(mk, org_id, name="Tiny Co", eligible=True)
    await _paid_invoice(mk, org_id, small, "100.00")
    # Over threshold but NOT eligible → excluded.
    corp = await _vendor(mk, org_id, name="Corp Co", eligible=False)
    await _paid_invoice(mk, org_id, corp, "9000.00")

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post("/api/tax/1099/file", json={"year": YEAR, "idempotency_key": "k2"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["submitted_count"] == 1
