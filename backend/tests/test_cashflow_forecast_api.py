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


async def _set_org_settings(realdb, key: str, mutate) -> None:
    """Apply `mutate(settings_dict)` to the tenant org's control-plane settings.

    The realdb fixture truncates TENANT tables but not the control-plane
    `organizations` row, so settings-mutating tests must reset what they set —
    each such test below restores the relevant block at the end."""
    from sqlalchemy import update

    from app.models.organization import Organization

    info = realdb.info(key)
    async with realdb.control_sessionmaker()() as s:
        org = await s.get(Organization, info.org_id)
        settings = dict(org.settings or {})
        mutate(settings)
        await s.execute(
            update(Organization).where(Organization.id == info.org_id).values(settings=settings)
        )
        await s.commit()


async def test_cash_position_auto_seeds_from_provider_balance(realdb):
    """With no opening_balance query param, the position seeds from the org's
    configured (mock) payment provider's deterministic balance."""
    await _add_invoice(
        realdb,
        "a",
        amount="50000",
        status=InvoiceStatus.approved.value,
        due_date=_TODAY + timedelta(days=10),
    )

    def _set_mock(settings):
        settings["payments"] = {"provider": "mock"}

    await _set_org_settings(realdb, "a", _set_mock)
    try:
        async with realdb.client(key="a", role="cfo") as c:
            resp = await c.get("/api/analytics/cash_position?granularity=month")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # Mock deterministic balance 250000 seeded automatically.
        assert body["opening_balance"] == 250000.0
        assert body["opening_balance_source"] == "provider"
        assert body["opening_balance_currency"] == "USD"
        assert body["periods"][0]["closing"] == 200000.0
    finally:
        await _set_org_settings(realdb, "a", lambda s: s.pop("payments", None))


async def test_cash_position_seed_balance_false_skips_provider(realdb):
    """`seed_balance=false` skips the provider call and falls through to the
    `none` source (no manual balance, no settings balance)."""

    def _set_mock(settings):
        settings["payments"] = {"provider": "mock"}

    await _set_org_settings(realdb, "a", _set_mock)
    try:
        async with realdb.client(key="a", role="cfo") as c:
            resp = await c.get("/api/analytics/cash_position?seed_balance=false")
        assert resp.status_code == 200
        body = resp.json()
        assert body["opening_balance"] == 0.0
        assert body["opening_balance_source"] == "none"
    finally:
        await _set_org_settings(realdb, "a", lambda s: s.pop("payments", None))


async def test_cash_position_query_balance_beats_provider(realdb):
    """An explicit opening_balance query param wins over the provider auto-sync."""

    def _set_mock(settings):
        settings["payments"] = {"provider": "mock"}

    await _set_org_settings(realdb, "a", _set_mock)
    try:
        async with realdb.client(key="a", role="cfo") as c:
            resp = await c.get("/api/analytics/cash_position?opening_balance=1000")
        assert resp.status_code == 200
        body = resp.json()
        assert body["opening_balance"] == 1000.0
        # `explicit` (not `query`) — this endpoint now shares the copilot's
        # resolution chain, so the four source values have one vocabulary.
        assert body["opening_balance_source"] == "explicit"
    finally:
        await _set_org_settings(realdb, "a", lambda s: s.pop("payments", None))


async def test_cash_position_refuses_a_foreign_currency_provider_balance(realdb):
    """A funding account denominated in something other than the org's reporting
    currency must NOT seed the curve — every outflow subtracted from it here is
    in the reporting currency, so mixing them makes the running balance a silent
    two-currency figure. The chain falls through and says why."""

    def _set(settings):
        settings["payments"] = {
            "provider": "mock",
            "balance": "9999.99",
            "balance_currency": "EUR",
        }
        settings["reporting_currency"] = "USD"
        settings["cashflow"] = {"opening_balance": "4321"}

    await _set_org_settings(realdb, "a", _set)
    try:
        async with realdb.client(key="a", role="cfo") as c:
            resp = await c.get("/api/analytics/cash_position")
        assert resp.status_code == 200
        body = resp.json()
        assert body["opening_balance"] == 4321.0
        assert body["opening_balance_source"] == "settings"
        # The refusal is visible — otherwise indistinguishable from "no bank".
        assert body["opening_balance_provider_skipped"] == "currency_mismatch"
        # The curve is always denominated in the reporting currency.
        assert body["opening_balance_currency"] == "USD"
    finally:

        def _clear(settings):
            settings.pop("payments", None)
            settings.pop("cashflow", None)
            settings.pop("reporting_currency", None)

        await _set_org_settings(realdb, "a", _clear)


async def test_cash_position_falls_back_to_settings_when_provider_unsupported(realdb):
    """Provider can't report a balance → fall through to the persisted
    settings.cashflow.opening_balance (source `settings`)."""

    def _set(settings):
        settings["payments"] = {"provider": "mock", "balance_available": False}
        settings["cashflow"] = {"opening_balance": "7777"}

    await _set_org_settings(realdb, "a", _set)
    try:
        async with realdb.client(key="a", role="cfo") as c:
            resp = await c.get("/api/analytics/cash_position")
        assert resp.status_code == 200
        body = resp.json()
        assert body["opening_balance"] == 7777.0
        assert body["opening_balance_source"] == "settings"
    finally:

        def _clear(settings):
            settings.pop("payments", None)
            settings.pop("cashflow", None)

        await _set_org_settings(realdb, "a", _clear)


async def test_cash_position_rejects_a_malformed_opening_balance_param(realdb):
    """A bad value the CLIENT sent is a client error — still the 400
    `_parse_decimal_param` has always raised, unchanged by the move to the
    shared resolver (the query param is parsed at the call site precisely so
    this boundary survives)."""
    async with realdb.client(key="a", role="cfo") as c:
        resp = await c.get("/api/analytics/cash_position?opening_balance=not-a-number")
    assert resp.status_code == 400


async def test_cash_position_degrades_on_a_corrupt_persisted_balance(realdb):
    """A corrupt value in the org's OWN stored settings is not the caller's
    fault, so it must not 422 the dashboard — the chain falls through to the
    next link and the UI prompts for a balance, matching how
    `resolve_cash_thresholds` already treats a corrupt stored threshold."""

    def _set(settings):
        settings["cashflow"] = {"opening_balance": "not-a-number"}

    await _set_org_settings(realdb, "a", _set)
    try:
        async with realdb.client(key="a", role="cfo") as c:
            resp = await c.get("/api/analytics/cash_position")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["opening_balance"] == 0.0
        assert body["opening_balance_source"] == "none"
    finally:
        await _set_org_settings(realdb, "a", lambda s: s.pop("cashflow", None))


async def test_cash_position_reads_persisted_threshold(realdb):
    """When the request passes no min_balance_threshold, the endpoint reads the
    org's persisted threshold and flags / collects breaches accordingly."""
    await _add_invoice(
        realdb,
        "a",
        amount="800",
        status=InvoiceStatus.approved.value,
        due_date=_TODAY + timedelta(days=10),
    )

    def _set(settings):
        settings["cashflow"] = {"min_balance_threshold": "500"}

    await _set_org_settings(realdb, "a", _set)
    try:
        async with realdb.client(key="a", role="cfo") as c:
            resp = await c.get(
                "/api/analytics/cash_position?opening_balance=1000&granularity=month"
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # Persisted threshold 500 applied (no query override): 1000-800=200 < 500.
        assert body["threshold"] == 500.0
        assert body["periods"][0]["below_threshold"] is True
        assert len(body["breaches"]) == 1
        assert body["breaches"][0]["shortfall"] == 300.0
    finally:
        await _set_org_settings(realdb, "a", lambda s: s.pop("cashflow", None))


# ---------------------------------------------------------------------------
# cash-position-settings (persisted thresholds GET/PUT)
# ---------------------------------------------------------------------------


async def test_cash_position_settings_round_trip(realdb):
    """PUT persists the threshold; GET reads it back; cash_position then applies
    it without a per-request override."""
    try:
        async with realdb.client(key="a", role="cfo") as c:
            # Default: nothing persisted.
            get0 = await c.get("/api/analytics/cash-position-settings")
            assert get0.status_code == 200
            assert get0.json()["min_balance_threshold"] is None

            put = await c.put(
                "/api/analytics/cash-position-settings",
                json={"min_balance_threshold": "2500.00"},
            )
            assert put.status_code == 200, put.text
            assert put.json()["min_balance_threshold"] == "2500.00"

            get1 = await c.get("/api/analytics/cash-position-settings")
            assert get1.json()["min_balance_threshold"] == "2500.00"
    finally:
        await _set_org_settings(realdb, "a", lambda s: s.pop("cashflow", None))


async def test_cash_position_settings_negative_rejected(realdb):
    async with realdb.client(key="a", role="cfo") as c:
        resp = await c.put(
            "/api/analytics/cash-position-settings",
            json={"min_balance_threshold": "-1"},
        )
    assert resp.status_code == 422


async def test_cash_position_settings_clerk_forbidden(realdb):
    async with realdb.client(key="a", role="ap_clerk") as c:
        assert (await c.get("/api/analytics/cash-position-settings")).status_code == 403
        assert (
            await c.put(
                "/api/analytics/cash-position-settings",
                json={"min_balance_threshold": "100"},
            )
        ).status_code == 403


async def test_cash_position_settings_admin_allowed(realdb):
    try:
        async with realdb.client(key="a", role="admin") as c:
            assert (await c.get("/api/analytics/cash-position-settings")).status_code == 200
            assert (
                await c.put(
                    "/api/analytics/cash-position-settings",
                    json={"min_balance_threshold": "100"},
                )
            ).status_code == 200
    finally:
        await _set_org_settings(realdb, "a", lambda s: s.pop("cashflow", None))


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
    assert body["opening_balance_source"] == "explicit"
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
# White-label branding on the analytics exports (CSV provenance + PDF)
# ---------------------------------------------------------------------------


async def test_export_csv_carries_brand_provenance_block(realdb):
    """The default CSV is prefixed with a `#`-comment provenance block (product
    name + report + generated-at); the data grid below still parses."""
    await _add_invoice(
        realdb,
        "a",
        amount="250",
        status=InvoiceStatus.approved.value,
        due_date=_TODAY + timedelta(days=10),
    )
    async with realdb.client(key="a", role="cfo") as c:
        resp = await c.get("/api/analytics/export/invoice_register")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/csv")
    lines = resp.text.splitlines()
    assert lines[0].startswith("# ")
    assert lines[0].endswith("Analytics Export")
    assert any(ln.startswith("# Report: invoice_register") for ln in lines)
    assert any(ln.startswith("# Generated:") for ln in lines)
    # The data grid is intact below the comment block.
    assert "invoice_id" in resp.text


async def test_export_invoice_register_pdf(realdb):
    """`format=pdf` returns application/pdf with a .pdf attachment filename."""
    await _add_invoice(
        realdb,
        "a",
        amount="250",
        status=InvoiceStatus.approved.value,
        due_date=_TODAY + timedelta(days=10),
    )
    async with realdb.client(key="a", role="cfo") as c:
        resp = await c.get("/api/analytics/export/invoice_register?format=pdf")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/pdf")
    assert "attachment" in resp.headers["content-disposition"]
    assert resp.headers["content-disposition"].endswith('.pdf"')
    assert resp.content.startswith(b"%PDF")


async def test_export_aging_snapshot_pdf(realdb):
    """A second report renders as PDF too (no rows needed — cover + table)."""
    async with realdb.client(key="a", role="cfo") as c:
        resp = await c.get("/api/analytics/export/aging_snapshot?format=pdf")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/pdf")
    assert resp.content.startswith(b"%PDF")


async def test_export_bad_format_422(realdb):
    """An unsupported `format` is rejected by the route validator."""
    async with realdb.client(key="a", role="cfo") as c:
        resp = await c.get("/api/analytics/export/invoice_register?format=xlsx")
    assert resp.status_code == 422


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
