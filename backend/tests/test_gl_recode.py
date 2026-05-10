"""Tests for `services.gl_recode.bulk_recode_gl`.

Mocks the DB session so the test suite stays hermetic. Two SELECTs hit
the DB on every run — `_load_active_chart` and `_select_eligible` —
plus an optional `_load_priors`. We stub them via a dispatch table so
each test only declares the rows that matter.

The AI fallback path is exercised via the `ai_runner` injection point.
We don't go anywhere near boto3 / Anthropic in these tests; that's
covered by the regular extraction test suite.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.invoice import InvoiceStatus
from app.services.gl_recode import (
    RecodeFilter,
    RecodeReport,
    bulk_recode_gl,
)

# ---------- Fixtures ------------------------------------------------------


def _make_invoice(
    *,
    vendor_id=None,
    gl_account=None,
    status=InvoiceStatus.ready_for_review,
    invoice_number="INV-1",
    invoice_date=None,
):
    return SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        vendor_id=vendor_id,
        gl_account=gl_account,
        status=status,
        invoice_number=invoice_number,
        vendor_name="Acme Corp",
        invoice_date=invoice_date,
        warnings=None,
    )


class _Stub:
    """Sequence-driven AsyncMock for db.execute().

    Each call returns the next pre-built result.  Result objects expose
    `.scalars().all()` and a callable `.all()` for the priors join."""

    def __init__(self, results: list):
        self._results = list(results)
        self._idx = 0

    async def __call__(self, *_args, **_kwargs):
        if self._idx >= len(self._results):
            raise AssertionError("execute() called more times than the test set up")
        out = self._results[self._idx]
        self._idx += 1
        return out


def _scalars_all(rows: list):
    """Fake .scalars().all() chain — returns `rows`."""
    obj = MagicMock()
    obj.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=rows)))
    return obj


def _all_rows(rows: list):
    """Fake plain `.all()` for tuple-returning queries (priors join)."""
    obj = MagicMock()
    obj.all = MagicMock(return_value=rows)
    return obj


def _make_db_for(*, active_codes, eligible_invoices, priors):
    db = MagicMock()
    db.commit = AsyncMock()
    db.add = MagicMock()
    db.execute = _Stub(
        [
            _scalars_all(active_codes),  # _load_active_chart
            _scalars_all(eligible_invoices),  # _select_eligible
            _all_rows([(vid, val) for vid, val in priors.items()]),  # _load_priors
        ]
    )
    return db


# ---------- Eligibility ---------------------------------------------------


@pytest.mark.asyncio
async def test_immutable_statuses_skipped():
    """Re-coding a posted/paid invoice would drift from the ERP.
    Skip every immutable status without trying anything."""
    org_id = uuid.uuid4()
    inv_paid = _make_invoice(vendor_id=uuid.uuid4(), status=InvoiceStatus.paid, gl_account="6100")
    inv_posted = _make_invoice(
        vendor_id=uuid.uuid4(), status=InvoiceStatus.posted_in_erp, gl_account="6100"
    )
    inv_ready = _make_invoice(
        vendor_id=uuid.uuid4(), status=InvoiceStatus.ready_for_review, gl_account="6100"
    )

    db = _make_db_for(
        active_codes=["6100", "6200"],
        eligible_invoices=[inv_paid, inv_posted, inv_ready],
        priors={inv_ready.vendor_id: "6200"},
    )

    report = await bulk_recode_gl(db, organization_id=org_id, filt=RecodeFilter(), dry_run=True)

    assert report.skipped_immutable == 2
    assert report.matched == 1  # only ready_for_review counts
    assert len(report.changes) == 1


@pytest.mark.asyncio
async def test_invoices_without_vendor_skipped():
    """No vendor → no prior lookup possible → can't help via the cheap
    path. Don't pull the AI lever just to cover unmatched invoices."""
    org_id = uuid.uuid4()
    inv_no_vendor = _make_invoice(vendor_id=None, gl_account=None)
    inv_with_vendor = _make_invoice(vendor_id=uuid.uuid4(), gl_account=None)

    db = _make_db_for(
        active_codes=["6100"],
        eligible_invoices=[inv_no_vendor, inv_with_vendor],
        priors={inv_with_vendor.vendor_id: "6100"},
    )
    report = await bulk_recode_gl(db, organization_id=org_id, filt=RecodeFilter(), dry_run=True)

    assert report.skipped_no_vendor == 1
    assert report.matched == 1
    assert len(report.changes) == 1
    assert report.changes[0].source == "vendor_prior"


# ---------- Priors path --------------------------------------------------


@pytest.mark.asyncio
async def test_prior_applied_when_in_active_chart_and_changes_value():
    """Happy priors path: cached '6200', invoice has '6100', chart has
    both — record the change, increment by_source['vendor_prior']."""
    vendor_id = uuid.uuid4()
    org_id = uuid.uuid4()
    inv = _make_invoice(vendor_id=vendor_id, gl_account="6100")

    db = _make_db_for(
        active_codes=["6100", "6200"],
        eligible_invoices=[inv],
        priors={vendor_id: "6200"},
    )

    report = await bulk_recode_gl(db, organization_id=org_id, filt=RecodeFilter(), dry_run=False)

    assert len(report.changes) == 1
    change = report.changes[0]
    assert change.old_gl == "6100"
    assert change.new_gl == "6200"
    assert change.source == "vendor_prior"
    assert report.by_source["vendor_prior"] == 1
    assert inv.gl_account == "6200"  # persisted in non-dry-run mode
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_dry_run_does_not_mutate_invoice():
    """Belt-and-braces: dry_run=True must leave inv.gl_account
    untouched even though the change is reported."""
    vendor_id = uuid.uuid4()
    inv = _make_invoice(vendor_id=vendor_id, gl_account=None)

    db = _make_db_for(
        active_codes=["6100"],
        eligible_invoices=[inv],
        priors={vendor_id: "6100"},
    )
    report = await bulk_recode_gl(
        db, organization_id=uuid.uuid4(), filt=RecodeFilter(), dry_run=True
    )

    assert len(report.changes) == 1
    assert inv.gl_account is None  # not persisted
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_prior_skipped_when_already_matches_invoice():
    """No-op invoices shouldn't pollute the change list; they go in the
    skipped_no_change bucket so the operator can see total churn vs.
    actual movement."""
    vendor_id = uuid.uuid4()
    inv = _make_invoice(vendor_id=vendor_id, gl_account="6100")

    db = _make_db_for(
        active_codes=["6100"],
        eligible_invoices=[inv],
        priors={vendor_id: "6100"},
    )
    report = await bulk_recode_gl(
        db, organization_id=uuid.uuid4(), filt=RecodeFilter(), dry_run=True
    )

    assert report.skipped_no_change == 1
    assert report.changes == []


@pytest.mark.asyncio
async def test_prior_with_stale_code_skipped_or_routed_to_ai():
    """A cached value pointing at a now-deactivated GL must not be
    applied. With AI fallback off it's just skipped; with AI on it
    becomes an AI candidate."""
    vendor_id = uuid.uuid4()
    inv = _make_invoice(vendor_id=vendor_id, gl_account=None)

    db = _make_db_for(
        active_codes=["6100"],  # 5500 no longer active
        eligible_invoices=[inv],
        priors={vendor_id: "5500"},
    )
    report = await bulk_recode_gl(
        db,
        organization_id=uuid.uuid4(),
        filt=RecodeFilter(),
        dry_run=True,
        include_ai_fallback=False,
    )

    assert report.skipped_invalid_code == 1
    assert report.skipped_no_prior_no_ai == 1  # routed to AI then dropped
    assert report.changes == []


@pytest.mark.asyncio
async def test_no_prior_skipped_when_ai_fallback_disabled():
    vendor_id = uuid.uuid4()
    inv = _make_invoice(vendor_id=vendor_id, gl_account=None)

    db = _make_db_for(
        active_codes=["6100"],
        eligible_invoices=[inv],
        priors={},  # no prior for this vendor
    )
    report = await bulk_recode_gl(
        db,
        organization_id=uuid.uuid4(),
        filt=RecodeFilter(),
        dry_run=True,
        include_ai_fallback=False,
    )

    assert report.skipped_no_prior_no_ai == 1
    assert report.changes == []


# ---------- AI fallback ---------------------------------------------------


@pytest.mark.asyncio
async def test_ai_fallback_invoked_only_when_enabled_and_no_prior():
    """Cheap path is preferred — the AI runner should NOT run for an
    invoice the prior covers."""
    vendor_with_prior = uuid.uuid4()
    vendor_without_prior = uuid.uuid4()
    inv_priored = _make_invoice(
        vendor_id=vendor_with_prior, gl_account=None, invoice_number="INV-A"
    )
    inv_no_prior = _make_invoice(
        vendor_id=vendor_without_prior, gl_account=None, invoice_number="INV-B"
    )

    async def fake_ai_runner(db_, inv, *, actor_id, org_settings, ctrl_db):
        inv.gl_account = "6100"  # AI assigns a valid code

    ai_mock = AsyncMock(side_effect=fake_ai_runner)

    db = _make_db_for(
        active_codes=["6100", "6200"],
        eligible_invoices=[inv_priored, inv_no_prior],
        priors={vendor_with_prior: "6200"},
    )

    report = await bulk_recode_gl(
        db,
        organization_id=uuid.uuid4(),
        filt=RecodeFilter(),
        dry_run=True,
        include_ai_fallback=True,
        ai_runner=ai_mock,
    )

    # AI runs once — only for inv_no_prior.
    assert ai_mock.await_count == 1
    assert ai_mock.await_args.args[1] is inv_no_prior

    # Both invoices show up as changes — one prior-sourced, one AI-sourced.
    sources = sorted(c.source for c in report.changes)
    assert sources == ["ai", "vendor_prior"]
    assert report.by_source == {"vendor_prior": 1, "ai": 1}


@pytest.mark.asyncio
async def test_ai_fallback_failure_counted_not_raised():
    """One AI call going wrong must not abort the whole bulk run."""
    vendor_id = uuid.uuid4()
    inv = _make_invoice(vendor_id=vendor_id, gl_account=None)

    async def boom(*_a, **_kw):
        raise RuntimeError("simulated quota exceeded")

    db = _make_db_for(active_codes=["6100"], eligible_invoices=[inv], priors={})

    report = await bulk_recode_gl(
        db,
        organization_id=uuid.uuid4(),
        filt=RecodeFilter(),
        dry_run=True,
        include_ai_fallback=True,
        ai_runner=AsyncMock(side_effect=boom),
    )
    assert report.skipped_ai_failed == 1
    assert report.changes == []


@pytest.mark.asyncio
async def test_ai_fallback_dry_run_rolls_back_speculative_write():
    """Dry-run must not persist via the AI path either. The runner can
    set inv.gl_account during the call, but we restore the old value
    so the caller can re-issue with dry_run=False knowing exactly what
    the report claimed."""
    vendor_id = uuid.uuid4()
    inv = _make_invoice(vendor_id=vendor_id, gl_account="6100")

    async def fake_ai_runner(db_, inv, *, actor_id, org_settings, ctrl_db):
        inv.gl_account = "6200"

    db = _make_db_for(
        active_codes=["6100", "6200"],
        eligible_invoices=[inv],
        priors={},
    )
    report = await bulk_recode_gl(
        db,
        organization_id=uuid.uuid4(),
        filt=RecodeFilter(),
        dry_run=True,
        include_ai_fallback=True,
        ai_runner=AsyncMock(side_effect=fake_ai_runner),
    )

    assert len(report.changes) == 1
    assert report.changes[0].new_gl == "6200"
    assert inv.gl_account == "6100"  # rolled back


# ---------- Audit log ----------------------------------------------------


@pytest.mark.asyncio
async def test_non_dry_run_writes_audit_log_per_change(monkeypatch):
    """Every persisted change goes through `services.audit.log_action`
    so the activity is reflected on the invoice's history. SOC 2 cares
    about this — bulk operations on financially-relevant fields can't
    be invisible."""
    from app.services import audit

    log_calls: list[dict] = []

    async def fake_log(db_, **kwargs):
        log_calls.append(kwargs)

    monkeypatch.setattr(audit, "log_action", fake_log)

    vendor_id = uuid.uuid4()
    inv = _make_invoice(vendor_id=vendor_id, gl_account=None)
    db = _make_db_for(
        active_codes=["6100"],
        eligible_invoices=[inv],
        priors={vendor_id: "6100"},
    )

    actor_id = uuid.uuid4()
    org_id = uuid.uuid4()
    await bulk_recode_gl(
        db,
        organization_id=org_id,
        filt=RecodeFilter(),
        dry_run=False,
        actor_id=actor_id,
    )

    assert len(log_calls) == 1
    call = log_calls[0]
    assert call["action"] == "invoice.gl_recoded"
    assert call["entity_type"] == "invoice"
    assert call["entity_id"] == inv.id
    assert call["actor_id"] == actor_id
    assert call["organization_id"] == org_id
    assert call["details"]["new_gl"] == "6100"
    assert call["details"]["source"] == "vendor_prior"


@pytest.mark.asyncio
async def test_dry_run_does_not_write_audit_log(monkeypatch):
    from app.services import audit

    log_calls: list[dict] = []

    async def fake_log(db_, **kwargs):
        log_calls.append(kwargs)

    monkeypatch.setattr(audit, "log_action", fake_log)

    vendor_id = uuid.uuid4()
    inv = _make_invoice(vendor_id=vendor_id, gl_account=None)
    db = _make_db_for(
        active_codes=["6100"],
        eligible_invoices=[inv],
        priors={vendor_id: "6100"},
    )
    await bulk_recode_gl(
        db,
        organization_id=uuid.uuid4(),
        filt=RecodeFilter(),
        dry_run=True,
    )
    assert log_calls == []


# ---------- Report shape ------------------------------------------------


def test_report_dict_uses_would_change_in_dry_run_else_applied():
    """The frontend toggles its banner copy based on the key name.
    Don't change this shape without coordinating with the UI."""
    dry = RecodeReport(dry_run=True).as_dict()
    assert "would_change" in dry and "applied" not in dry

    wet = RecodeReport(dry_run=False).as_dict()
    assert "applied" in wet and "would_change" not in wet
