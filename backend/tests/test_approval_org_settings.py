"""Every approval door must hand ``review.approve_invoice`` the org's settings.

``approve_invoice`` reads ``org_settings`` for four separate controls:

* the org's ``fraud_rules`` — which rules raise an exception, and three of those
  exception types (``duplicate`` / ``fraud_flag`` / ``line_total_mismatch``)
  BLOCK a payment run;
* ``matching`` — the per-vendor / per-commodity PO tolerance the
  approve-with-corrections `refresh_warnings` recomputes `invoice.po_match` at;
* ``exceptions`` — the auto-assign + SLA routing for any exception it opens;
* ``fraud_rules.structuring_*`` — the rolling same-vendor window the
  max-amount / CFO gates are measured against.

Passing ``None`` silently reverts all four to the platform defaults *for that
door only*, which is worse than a consistent default: a rule an org explicitly
turned off still opened a payment-blocking ``fraud_flag`` on approve, and a
stricter-than-default PO tolerance was erased from the row by the very approval
that should have honoured it. `POST /api/invoices/bulk/status` always threaded
it; the single-invoice endpoint, the email link, the Slack button and the Teams
card did not.

Two layers: a behavioural realdb test through the HTTP endpoint, and a source
scan so a NEW approval surface can't drop the kwarg again unnoticed (the same
drift-guard shape as `test_payment_methods.py` / `test_exception_type_labels.py`).
"""

from __future__ import annotations

import ast
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models.exception import Exception as APException
from app.models.invoice import Invoice, InvoiceStatus
from app.models.organization import Organization

# ---------------------------------------------------------------------------
# Behaviour — a disabled fraud rule stays disabled through the approve endpoint
# ---------------------------------------------------------------------------


async def _set_fraud_rules(realdb, org_id, rules: dict) -> None:
    async with realdb.control_sessionmaker()() as s:
        org = (await s.execute(select(Organization).where(Organization.id == org_id))).scalar_one()
        settings = dict(org.settings or {})
        settings["fraud_rules"] = {**(settings.get("fraud_rules") or {}), **rules}
        org.settings = settings
        await s.commit()


async def _seed_ready_for_review(mk, org_id, *, number: str, amount: str) -> uuid.UUID:
    inv_id = uuid.uuid4()
    async with mk() as s:
        s.add(
            Invoice(
                id=inv_id,
                organization_id=org_id,
                invoice_number=number,
                vendor_name="Org Settings Vendor",
                amount=Decimal(amount),
                currency="USD",
                status=InvoiceStatus.ready_for_review,
            )
        )
        await s.commit()
    return inv_id


async def _fraud_flags(mk, inv_id) -> list[APException]:
    async with mk() as s:
        return list(
            (
                await s.execute(
                    select(APException).where(
                        APException.invoice_id == inv_id,
                        APException.exception_type == "fraud_flag",
                    )
                )
            )
            .scalars()
            .all()
        )


@pytest.mark.asyncio
async def test_approve_honours_a_disabled_fraud_rule(realdb):
    """`round_amount_enabled: false` must survive an approve-with-corrections.

    A $5,000.00 invoice trips the round-amount rule at the platform default. The
    org turned that rule OFF, so the approval must not open a `fraud_flag` —
    which would refuse the invoice's own payment run.
    """
    info = realdb.info("a")
    mk = realdb.sessionmaker("a")
    await _set_fraud_rules(realdb, info.org_id, {"round_amount_enabled": False})
    inv_id = await _seed_ready_for_review(mk, info.org_id, number="ORGSET-1", amount="5000.00")

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(
            f"/api/invoices/{inv_id}/approve", json={"description": "corrected description"}
        )
    assert resp.status_code == 200, resp.text

    assert await _fraud_flags(mk, inv_id) == [], (
        "a fraud rule the org disabled must not open a payment-blocking exception"
    )


@pytest.mark.asyncio
async def test_approve_still_raises_an_enabled_fraud_rule(realdb):
    """The counterpart — the wiring must not have turned the rule off for
    everyone. With the rule left at its default the same approval DOES flag."""
    info = realdb.info("a")
    mk = realdb.sessionmaker("a")
    await _set_fraud_rules(realdb, info.org_id, {"round_amount_enabled": True})
    inv_id = await _seed_ready_for_review(mk, info.org_id, number="ORGSET-2", amount="5000.00")

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(
            f"/api/invoices/{inv_id}/approve", json={"description": "corrected description"}
        )
    assert resp.status_code == 200, resp.text

    assert await _fraud_flags(mk, inv_id), "an enabled fraud rule must still raise"


# ---------------------------------------------------------------------------
# Drift guard — no approval door may call approve_invoice without org_settings
# ---------------------------------------------------------------------------

_API_DIR = Path(__file__).resolve().parents[1] / "app" / "api"


def _approve_calls_missing_org_settings(path: Path) -> list[int]:
    """Line numbers of `approve_invoice(...)` calls with no `org_settings=`."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    missing: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (
            func.attr
            if isinstance(func, ast.Attribute)
            else func.id
            if isinstance(func, ast.Name)
            else None
        )
        if name != "approve_invoice":
            continue
        keywords = {kw.arg for kw in node.keywords}
        if "org_settings" not in keywords:
            missing.append(node.lineno)
    return missing


def test_every_api_approval_door_passes_org_settings():
    offenders: list[str] = []
    for path in sorted(_API_DIR.rglob("*.py")):
        for lineno in _approve_calls_missing_org_settings(path):
            offenders.append(f"{path.relative_to(_API_DIR.parents[2])}:{lineno}")
    assert not offenders, (
        "these approve_invoice() call sites drop `org_settings`, so they silently "
        "evaluate the org's fraud rules, PO tolerances, exception routing and the "
        "structuring window at the PLATFORM defaults: " + ", ".join(offenders)
    )


def test_the_drift_guard_can_actually_fail(tmp_path):
    """The scan is only a guard if it detects the omission it exists to catch."""
    sample = tmp_path / "sample.py"
    sample.write_text(
        "async def door(db, invoice, user):\n"
        "    await review_svc.approve_invoice(db, invoice, actor_id=user.id)\n",
        encoding="utf-8",
    )
    assert _approve_calls_missing_org_settings(sample) == [2]
