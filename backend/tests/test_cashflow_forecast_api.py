"""Cash-flow forecasting API tests — the three CFO endpoints added under
`backend/app/api/analytics.py`:

  - GET /api/analytics/cashflow_forecast
  - GET /api/analytics/cashflow_whatif
  - GET /api/analytics/cash_position
  - GET /api/analytics/export/cashflow_forecast (CSV)

Real-Postgres harness (`realdb`): two persistent test tenants `a` / `b`.
Each test seeds its own invoices (+ optional PaymentSchedule) directly into
the tenant DB, then drives the endpoint through the ASGI client.

Covers:
  - RBAC: CFO + admin allowed; ap_clerk → 403; unauthenticated → 401
  - forecast happy path buckets committed vs pending; excludes terminal
  - whatif early captures discount; granularity validation → 422
  - cash_position BYO opening balance + threshold breaches + source flag
  - CSV export content-type + header; unknown report → 404
  - tenant isolation: tenant a's invoices invisible under tenant b
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

from app.models.invoice import Invoice, InvoiceStatus
from app.models.payment import PaymentSchedule

# The `realdb` fixture TRUNCATEs every tenant table at setup, so each test
# starts from a clean slate — no per-test cleanup fixture needed (and a
# second concurrent DELETE/TRUNCATE would deadlock against the fixture's).
_TODAY = date.today()


async def _add_invoice(
    realdb,
    key: str,
    *,
    amount,
    status,
    due_date,
    discount_date=None,
    discount_percent=None,
) -> uuid.UUID:
    """Insert one Invoice (+ optional PaymentSchedule) into the tenant DB."""
    mk = realdb.sessionmaker(key)
    async with mk() as s:
        inv = Invoice(
            organization_id=realdb.info(key).org_id,
            invoice_number=f"CF-{uuid.uuid4().hex[:8]}",
            vendor_name="Acme Supplies",
            amount=Decimal(str(amount)),
            status=status,
            invoice_date=_TODAY - timedelta(days=5),
            due_date=due_date,
        )
        s.add(inv)
        await s.flush()
        if discount_date is not None or discount_percent is not None:
            s.add(
                PaymentSchedule(
                    invoice_id=inv.id,
                    due_date=due_date,
                    discount_date=discount_date,
                    discount_percent=(
                        Decimal(str(discount_percent)) if discount_percent is not None else None
                    ),
                )
            )
        await s.commit()
        return inv.id


# ---------------------------------------------------------------------------
# cashflow_forecast
# ---------------------------------------------------------------------------


async def test_forecast_buckets_committed_and_pending(realdb):
    await _add_invoice(
        realdb,
        "a",
        amount="1000",
        status=InvoiceStatus.approved.value,
        due_date=_TODAY + timedelta(days=10),
    )
    await _add_invoice(
        realdb,
        "a",
        amount="500",
        status=InvoiceStatus.pending.value,
        due_date=_TODAY + timedelta(days=12),
    )
    async with realdb.client(key="a", role="cfo") as c:
        resp = await c.get("/api/analytics/cashflow_forecast?granularity=month&horizon_days=90")
    assert resp.status_code == 200
    body = resp.json()
    assert body["granularity"] == "month"
    assert body["totals"]["committed_amount"] == 1000.0
    assert body["totals"]["pending_amount"] == 500.0
    assert body["totals"]["scheduled_amount"] == 1500.0


async def test_forecast_excludes_terminal_and_paid(realdb):
    """paid / done / rejected / failed invoices must not appear in the
    projected-outflow total."""
    for status in (
        InvoiceStatus.paid.value,
        InvoiceStatus.done.value,
        InvoiceStatus.rejected.value,
        InvoiceStatus.failed.value,
    ):
        await _add_invoice(
            realdb,
            "a",
            amount="999",
            status=status,
            due_date=_TODAY + timedelta(days=10),
        )
    async with realdb.client(key="a", role="cfo") as c:
        resp = await c.get("/api/analytics/cashflow_forecast")
    assert resp.status_code == 200
    assert resp.json()["totals"]["scheduled_amount"] == 0.0


async def test_forecast_include_pending_false_drops_pipeline(realdb):
    await _add_invoice(
        realdb,
        "a",
        amount="500",
        status=InvoiceStatus.pending.value,
        due_date=_TODAY + timedelta(days=10),
    )
    async with realdb.client(key="a", role="cfo") as c:
        resp = await c.get("/api/analytics/cashflow_forecast?include_pending=false")
    assert resp.status_code == 200
    assert resp.json()["totals"]["scheduled_amount"] == 0.0


async def test_forecast_bad_granularity_422(realdb):
    async with realdb.client(key="a", role="cfo") as c:
        resp = await c.get("/api/analytics/cashflow_forecast?granularity=fortnight")
    assert resp.status_code == 422


async def test_forecast_admin_allowed_clerk_forbidden(realdb):
    async with realdb.client(key="a", role="admin") as c:
        assert (await c.get("/api/analytics/cashflow_forecast")).status_code == 200
    async with realdb.client(key="a", role="ap_clerk") as c:
        assert (await c.get("/api/analytics/cashflow_forecast")).status_code == 403


async def test_forecast_requires_auth(realdb):
    async with realdb.client(key="a", role=None) as c:
        assert (await c.get("/api/analytics/cashflow_forecast")).status_code == 401


# ---------------------------------------------------------------------------
# cashflow_whatif
# ---------------------------------------------------------------------------


async def test_whatif_early_captures_discount(realdb):
    await _add_invoice(
        realdb,
        "a",
        amount="1000",
        status=InvoiceStatus.approved.value,
        due_date=_TODAY + timedelta(days=20),
        discount_date=_TODAY + timedelta(days=5),
        discount_percent="2",
    )
    async with realdb.client(key="a", role="cfo") as c:
        resp = await c.get("/api/analytics/cashflow_whatif")
    assert resp.status_code == 200
    scenarios = resp.json()["scenarios"]
    assert scenarios["early"]["total_discount_captured"] == 20.0
    assert scenarios["early"]["total_outflow"] == 980.0
    assert scenarios["on_time"]["total_outflow"] == 1000.0
    assert scenarios["late"]["total_outflow"] == 1000.0


async def test_whatif_clerk_forbidden(realdb):
    async with realdb.client(key="a", role="ap_clerk") as c:
        assert (await c.get("/api/analytics/cashflow_whatif")).status_code == 403


# ---------------------------------------------------------------------------
# cash_position
# ---------------------------------------------------------------------------


async def test_cash_position_with_opening_balance_and_breach(realdb):
    await _add_invoice(
        realdb,
        "a",
        amount="800",
        status=InvoiceStatus.approved.value,
        due_date=_TODAY + timedelta(days=10),
    )
    async with realdb.client(key="a", role="cfo") as c:
        resp = await c.get(
            "/api/analytics/cash_position"
            "?opening_balance=1000&min_balance_threshold=500&granularity=month"
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["opening_balance"] == 1000.0
    assert body["opening_balance_source"] == "query"
    assert body["periods"][0]["closing"] == 200.0
    assert body["periods"][0]["below_threshold"] is True
    assert len(body["breaches"]) == 1
    assert body["breaches"][0]["shortfall"] == 300.0


async def test_cash_position_defaults_to_zero_with_source_none(realdb):
    async with realdb.client(key="a", role="cfo") as c:
        resp = await c.get("/api/analytics/cash_position")
    assert resp.status_code == 200
    body = resp.json()
    assert body["opening_balance"] == 0.0
    assert body["opening_balance_source"] == "none"


async def test_cash_position_bad_opening_balance_400(realdb):
    async with realdb.client(key="a", role="cfo") as c:
        resp = await c.get("/api/analytics/cash_position?opening_balance=notamoney")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------


async def test_export_cashflow_forecast_csv(realdb):
    await _add_invoice(
        realdb,
        "a",
        amount="1000",
        status=InvoiceStatus.approved.value,
        due_date=_TODAY + timedelta(days=10),
    )
    async with realdb.client(key="a", role="cfo") as c:
        resp = await c.get("/api/analytics/export/cashflow_forecast?granularity=month")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]
    assert "scheduled_amount" in resp.text
    assert "1000.00" in resp.text


async def test_export_unknown_report_404(realdb):
    async with realdb.client(key="a", role="cfo") as c:
        resp = await c.get("/api/analytics/export/does_not_exist")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


async def test_forecast_tenant_isolation(realdb):
    """An invoice seeded under tenant `a` must not surface when the
    forecast is queried under tenant `b`."""
    await _add_invoice(
        realdb,
        "a",
        amount="1234",
        status=InvoiceStatus.approved.value,
        due_date=_TODAY + timedelta(days=10),
    )
    async with realdb.client(key="b", role="cfo") as c:
        resp = await c.get("/api/analytics/cashflow_forecast")
    assert resp.status_code == 200
    assert resp.json()["totals"]["scheduled_amount"] == 0.0
