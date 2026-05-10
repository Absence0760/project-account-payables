"""Tests for the new fraud-detection rules in `services.invoice_warnings`.

Covers the five additive rules introduced for the
"Enhanced Fraud Detection" roadmap section:

  - Rush payment pattern (short due window)
  - Personal email domain on the linked vendor
  - New vendor + large amount
  - Bank/remit-to address change vs prior approved invoice
  - Statistical amount anomaly vs vendor history

Each rule is exercised via `refresh_warnings` against a mocked
AsyncSession so the tests stay hermetic. Org-config plumbing is
covered via the `_fraud_config` helper (which merges org settings
over `_DEFAULT_FRAUD_RULES`).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------- Helpers --------------------------------------------------------


def _invoice(**overrides):
    base = dict(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        vendor_name="Acme Corp",
        invoice_number="INV-100",
        amount=Decimal("500.00"),
        currency="USD",
        invoice_date=date(2026, 5, 1),
        due_date=date(2026, 5, 15),
        status="new",
        po_number=None,
        po_match=None,
        warnings=None,
        vendor_id=uuid.uuid4(),
        remit_to_address=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _vendor(**overrides):
    base = dict(
        id=uuid.uuid4(),
        name="Acme Corp",
        email="ap@acme.com",
        status="active",
        created_at=datetime.now(UTC) - timedelta(days=200),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _make_db(*, vendor=None, dup_count=0, prior_remit=None, history_amounts=None):
    """AsyncMock session whose `execute()` returns canned scalars in
    the order `refresh_warnings` issues them.

    Order of DB calls in `refresh_warnings`:
      1. Duplicate count                 → scalar()
      2. Vendor row                      → scalar_one_or_none()
      3. Prior remit-to                  → scalar_one_or_none()  (only when bank rule applies)
      4. Historical amounts              → scalars().all()       (only when stat rule applies)

    Implemented via a stateful side_effect function: an AsyncMock with
    a list side_effect plays the elements through `_execute_mock_call`,
    which (depending on configuration) can intercept the awaited return
    value and produce a default MagicMock instead of our pre-built
    result. A function side_effect is unambiguous — whatever we return
    is what `await db.execute(...)` resolves to.
    """
    history_amounts = history_amounts if history_amounts is not None else []

    def _result(scalar=None, scalar_one=None, scalars_all=None):
        r = MagicMock()
        r.scalar = MagicMock(return_value=scalar)
        r.scalar_one_or_none = MagicMock(return_value=scalar_one)
        r.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=scalars_all or [])))
        return r

    async def _execute(*args, **_kw):
        # Dispatch by query shape rather than call-order so an
        # `_ensure_exception` count check (also a `select(func.count())`)
        # doesn't shift the vendor-lookup slot. Heuristic: stringify the
        # SQL and key off the FROM table.
        q = str(args[0]).lower() if args else ""
        if "from invoices" in q and "count(" in q:
            return _result(scalar=dup_count)
        if "from vendors" in q:
            return _result(scalar_one=vendor)
        if "remit_to_address" in q:
            return _result(scalar_one=prior_remit)
        if "from invoices" in q and "amount" in q:
            return _result(scalars_all=history_amounts)
        if "from exceptions" in q or "count(" in q:
            return _result(scalar=0)  # no prior exception
        return _result()

    db = MagicMock()
    db.execute = AsyncMock(side_effect=_execute)
    db.add = MagicMock()
    return db


def _types(warnings):
    return {w["type"] for w in warnings}


# ---------- Config helper --------------------------------------------------


def test_fraud_config_returns_defaults_when_no_overrides():
    from app.services.invoice_warnings import _DEFAULT_FRAUD_RULES, _fraud_config

    assert _fraud_config(None) == _DEFAULT_FRAUD_RULES
    assert _fraud_config({}) == _DEFAULT_FRAUD_RULES
    assert _fraud_config({"unrelated": "x"}) == _DEFAULT_FRAUD_RULES


def test_fraud_config_org_settings_override_defaults():
    """The merge is shallow per-key: the org sets only what it cares
    about, the rest keeps its default."""
    from app.services.invoice_warnings import _fraud_config

    cfg = _fraud_config(
        {
            "fraud_rules": {
                "rush_payment_max_days": 1,
                "round_amount_enabled": False,
            }
        }
    )
    assert cfg["rush_payment_max_days"] == 1
    assert cfg["round_amount_enabled"] is False
    # Unspecified keys inherit defaults.
    assert cfg["future_date_enabled"] is True
    assert cfg["new_vendor_max_age_days"] == 30


def test_fraud_config_drops_unknown_keys_silently():
    """An old client that POSTs a dropped key shouldn't break the merge."""
    from app.services.invoice_warnings import _DEFAULT_FRAUD_RULES, _fraud_config

    cfg = _fraud_config({"fraud_rules": {"defunct_old_key": "x"}})
    assert "defunct_old_key" not in cfg
    assert cfg == _DEFAULT_FRAUD_RULES


# ---------- Rush payment ---------------------------------------------------


@pytest.mark.asyncio
async def test_rush_payment_flagged_when_due_in_short_window():
    from app.services.invoice_warnings import refresh_warnings

    inv = _invoice(invoice_date=date(2026, 5, 1), due_date=date(2026, 5, 3))
    db = _make_db(vendor=_vendor())
    warnings = await refresh_warnings(db, inv)
    assert "fraud_rush_payment" in _types(warnings)


@pytest.mark.asyncio
async def test_rush_payment_not_flagged_when_window_is_normal():
    from app.services.invoice_warnings import refresh_warnings

    inv = _invoice(invoice_date=date(2026, 5, 1), due_date=date(2026, 6, 1))
    db = _make_db(vendor=_vendor())
    warnings = await refresh_warnings(db, inv)
    assert "fraud_rush_payment" not in _types(warnings)


@pytest.mark.asyncio
async def test_rush_payment_respects_org_threshold():
    """Org-tightened threshold (max_days=1): a 2-day window is no
    longer rush. Loosening the rule shouldn't generate noise."""
    from app.services.invoice_warnings import refresh_warnings

    inv = _invoice(invoice_date=date(2026, 5, 1), due_date=date(2026, 5, 3))
    db = _make_db(vendor=_vendor())
    org_settings = {"fraud_rules": {"rush_payment_max_days": 1}}
    warnings = await refresh_warnings(db, inv, org_settings=org_settings)
    assert "fraud_rush_payment" not in _types(warnings)


@pytest.mark.asyncio
async def test_rush_payment_disabled_via_org_setting():
    from app.services.invoice_warnings import refresh_warnings

    inv = _invoice(invoice_date=date(2026, 5, 1), due_date=date(2026, 5, 3))
    db = _make_db(vendor=_vendor())
    org_settings = {"fraud_rules": {"rush_payment_enabled": False}}
    warnings = await refresh_warnings(db, inv, org_settings=org_settings)
    assert "fraud_rush_payment" not in _types(warnings)


# ---------- Personal email -------------------------------------------------


@pytest.mark.asyncio
async def test_personal_email_flag_for_gmail_vendor():
    from app.services.invoice_warnings import refresh_warnings

    vendor = _vendor(email="acme.payments@gmail.com")
    db = _make_db(vendor=vendor)
    warnings = await refresh_warnings(db, _invoice())
    assert "fraud_personal_email" in _types(warnings)


@pytest.mark.asyncio
async def test_personal_email_not_flagged_for_business_domain():
    from app.services.invoice_warnings import refresh_warnings

    vendor = _vendor(email="ap@acme.com")
    db = _make_db(vendor=vendor)
    warnings = await refresh_warnings(db, _invoice())
    assert "fraud_personal_email" not in _types(warnings)


@pytest.mark.asyncio
async def test_personal_email_handles_missing_email_field():
    """Vendor with no email shouldn't crash the refresh — just skip
    this rule."""
    from app.services.invoice_warnings import refresh_warnings

    vendor = _vendor(email=None)
    db = _make_db(vendor=vendor)
    warnings = await refresh_warnings(db, _invoice())
    assert "fraud_personal_email" not in _types(warnings)


@pytest.mark.asyncio
async def test_personal_email_org_can_extend_blocklist():
    """An org wanting to flag a specific competitor or country domain
    can append to the list. Defaults still apply on top."""
    from app.services.invoice_warnings import refresh_warnings

    vendor = _vendor(email="contact@suspicious.example")
    db = _make_db(vendor=vendor)
    org_settings = {"fraud_rules": {"personal_email_domains": ["gmail.com", "suspicious.example"]}}
    warnings = await refresh_warnings(db, _invoice(), org_settings=org_settings)
    assert "fraud_personal_email" in _types(warnings)


# ---------- New vendor + large amount -------------------------------------


@pytest.mark.asyncio
async def test_new_vendor_large_flagged():
    from app.services.invoice_warnings import refresh_warnings

    vendor = _vendor(created_at=datetime.now(UTC) - timedelta(days=2))
    inv = _invoice(amount=Decimal("25000"))
    db = _make_db(vendor=vendor)
    warnings = await refresh_warnings(db, inv)
    assert "fraud_new_vendor_large" in _types(warnings)


@pytest.mark.asyncio
async def test_new_vendor_small_amount_not_flagged():
    from app.services.invoice_warnings import refresh_warnings

    vendor = _vendor(created_at=datetime.now(UTC) - timedelta(days=2))
    inv = _invoice(amount=Decimal("500"))
    db = _make_db(vendor=vendor)
    warnings = await refresh_warnings(db, inv)
    assert "fraud_new_vendor_large" not in _types(warnings)


@pytest.mark.asyncio
async def test_old_vendor_large_amount_not_flagged_by_this_rule():
    """Established vendor billing a big-but-normal amount; this rule
    should leave it alone (the stat-anomaly rule may still fire)."""
    from app.services.invoice_warnings import refresh_warnings

    vendor = _vendor(created_at=datetime.now(UTC) - timedelta(days=400))
    inv = _invoice(amount=Decimal("25000"))
    db = _make_db(vendor=vendor)
    warnings = await refresh_warnings(db, inv)
    assert "fraud_new_vendor_large" not in _types(warnings)


# ---------- Bank / remit-to change -----------------------------------------


@pytest.mark.asyncio
async def test_bank_change_flagged_when_remit_differs():
    from app.services.invoice_warnings import refresh_warnings

    inv = _invoice(remit_to_address="555 New Bank St, Dover, DE")
    vendor = _vendor()
    db = _make_db(vendor=vendor, prior_remit="100 Old Bank Ave, Wilmington, DE")
    warnings = await refresh_warnings(db, inv)
    assert "fraud_bank_change" in _types(warnings)


@pytest.mark.asyncio
async def test_bank_change_not_flagged_when_remit_unchanged():
    from app.services.invoice_warnings import refresh_warnings

    addr = "100 Old Bank Ave, Wilmington, DE"
    inv = _invoice(remit_to_address=addr)
    vendor = _vendor()
    db = _make_db(vendor=vendor, prior_remit=addr)
    warnings = await refresh_warnings(db, inv)
    assert "fraud_bank_change" not in _types(warnings)


@pytest.mark.asyncio
async def test_bank_change_no_prior_history_does_not_flag():
    """A first-ever invoice for a vendor obviously can't be a 'change'."""
    from app.services.invoice_warnings import refresh_warnings

    inv = _invoice(remit_to_address="100 Initial Address, Wilmington, DE")
    db = _make_db(vendor=_vendor(), prior_remit=None)
    warnings = await refresh_warnings(db, inv)
    assert "fraud_bank_change" not in _types(warnings)


@pytest.mark.asyncio
async def test_bank_change_severity_is_error():
    """Bank-redirect attacks are the highest-severity fraud signal —
    the warning level matters for the exception-queue routing."""
    from app.services.invoice_warnings import refresh_warnings

    inv = _invoice(remit_to_address="555 New")
    db = _make_db(vendor=_vendor(), prior_remit="100 Old")
    warnings = await refresh_warnings(db, inv)
    bank = next(w for w in warnings if w["type"] == "fraud_bank_change")
    assert bank["severity"] == "error"


# ---------- Statistical anomaly -------------------------------------------


@pytest.mark.asyncio
async def test_stat_anomaly_flagged_when_amount_spikes():
    """Vendor history mean ~$1000, stdev tight; a $10000 invoice is
    way more than 2σ above the mean."""
    from app.services.invoice_warnings import refresh_warnings

    inv = _invoice(amount=Decimal("10000"))
    history = [Decimal("950"), Decimal("1000"), Decimal("1050"), Decimal("1000"), Decimal("975")]
    db = _make_db(vendor=_vendor(), history_amounts=history)
    warnings = await refresh_warnings(db, inv)
    assert "fraud_stat_anomaly" in _types(warnings)


@pytest.mark.asyncio
async def test_stat_anomaly_not_flagged_when_amount_in_pattern():
    from app.services.invoice_warnings import refresh_warnings

    inv = _invoice(amount=Decimal("1020"))
    history = [Decimal("950"), Decimal("1000"), Decimal("1050"), Decimal("1000"), Decimal("975")]
    db = _make_db(vendor=_vendor(), history_amounts=history)
    warnings = await refresh_warnings(db, inv)
    assert "fraud_stat_anomaly" not in _types(warnings)


@pytest.mark.asyncio
async def test_stat_anomaly_skipped_when_history_too_short():
    """We need enough history to compute a meaningful mean/stdev. Two
    invoices isn't enough — every other one would look anomalous."""
    from app.services.invoice_warnings import refresh_warnings

    inv = _invoice(amount=Decimal("10000"))
    history = [Decimal("100"), Decimal("110")]
    db = _make_db(vendor=_vendor(), history_amounts=history)
    warnings = await refresh_warnings(db, inv)
    assert "fraud_stat_anomaly" not in _types(warnings)


@pytest.mark.asyncio
async def test_stat_anomaly_org_can_tighten_sigma():
    """Default sigma is 2; lowering to 1 makes the rule fire on
    smaller jumps. Useful for risk-averse orgs."""
    from app.services.invoice_warnings import refresh_warnings

    inv = _invoice(amount=Decimal("1300"))
    history = [Decimal("950"), Decimal("1000"), Decimal("1050"), Decimal("1000"), Decimal("975")]
    db = _make_db(vendor=_vendor(), history_amounts=history)
    org_settings = {"fraud_rules": {"stat_anomaly_sigma": 1.0}}
    warnings = await refresh_warnings(db, inv, org_settings=org_settings)
    assert "fraud_stat_anomaly" in _types(warnings)


# ---------- Config-driven master-switch behaviour -------------------------


@pytest.mark.asyncio
async def test_all_fraud_rules_disabled_via_org_settings():
    """An org can globally suppress every new rule via per-rule
    enable flags. Existing built-in rules (duplicate, missing fields)
    keep firing — those aren't part of fraud_rules."""
    from app.services.invoice_warnings import refresh_warnings

    inv = _invoice(
        invoice_date=date(2026, 5, 1),
        due_date=date(2026, 5, 2),
        amount=Decimal("25000"),
        remit_to_address="555 New",
    )
    vendor = _vendor(email="x@gmail.com", created_at=datetime.now(UTC) - timedelta(days=2))
    db = _make_db(
        vendor=vendor,
        prior_remit="100 Old",
        history_amounts=[Decimal("100"), Decimal("110"), Decimal("105")],
    )
    org_settings = {
        "fraud_rules": {
            "round_amount_enabled": False,
            "future_date_enabled": False,
            "bank_change_enabled": False,
            "stat_anomaly_enabled": False,
            "rush_payment_enabled": False,
            "new_vendor_large_enabled": False,
            "personal_email_enabled": False,
        }
    }
    warnings = await refresh_warnings(db, inv, org_settings=org_settings)
    fraud_types = {t for t in _types(warnings) if t.startswith("fraud_")}
    assert fraud_types == set()
