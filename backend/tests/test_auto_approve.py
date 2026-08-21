"""Tests for auto-approve decision logic wired through extraction.py.

The actual HTTP path (run_extraction) is integration-heavy and requires
real DB engines, so we test the supporting pure/near-pure pieces instead:

- get_step_config from workflow_engine — the function the extraction path
  calls to read extraction and approval step configs.
- VALID_TRANSITIONS — the state machine table that must contain the
  auto-approve transitions for the logic in extraction.py to be legal.

These guard the three conditions that trigger auto-approve:
1. confidence >= auto_approve_threshold  → pending/new → approved
2. amount < auto_approve_below           → pending/new → approved
Both paths call transition_invoice with InvoiceStatus.approved, which
validates against VALID_TRANSITIONS before mutating the DB row.
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# get_step_config
# ---------------------------------------------------------------------------


def test_get_step_config_returns_extraction_config():
    """get_step_config correctly retrieves the config for a named step type."""
    from app.services.workflow_engine import get_step_config

    snapshot = {
        "steps": [
            {
                "type": "extraction",
                "config": {
                    "auto_approve_enabled": True,
                    "auto_approve_threshold": 0.90,
                },
            },
            {
                "type": "approval",
                "config": {
                    "required": True,
                },
            },
        ]
    }

    config = get_step_config(snapshot, "extraction")

    assert config["auto_approve_enabled"] is True
    assert config["auto_approve_threshold"] == 0.90


def test_get_step_config_returns_approval_config():
    """get_step_config finds the approval step among multiple steps."""
    from app.services.workflow_engine import get_step_config

    snapshot = {
        "steps": [
            {"type": "extraction", "config": {"auto_approve_enabled": False}},
            {"type": "approval", "config": {"auto_approve_below": 500.0}},
        ]
    }

    config = get_step_config(snapshot, "approval")

    assert config["auto_approve_below"] == 500.0


def test_get_step_config_returns_empty_for_missing_step():
    """When the requested step type is absent, get_step_config returns an empty dict."""
    from app.services.workflow_engine import get_step_config

    snapshot = {
        "steps": [
            {"type": "erp_export", "config": {"export_format": "json"}},
        ]
    }

    config = get_step_config(snapshot, "approval")

    assert config == {}


def test_get_step_config_returns_empty_for_step_without_config_key():
    """A step entry that has no 'config' key returns an empty dict, not a KeyError."""
    from app.services.workflow_engine import get_step_config

    snapshot = {
        "steps": [
            {"type": "extraction"},  # no "config" key
        ]
    }

    config = get_step_config(snapshot, "extraction")

    assert config == {}


def test_get_step_config_returns_empty_for_empty_steps_list():
    """An empty steps list returns empty dict for any step type."""
    from app.services.workflow_engine import get_step_config

    config = get_step_config({"steps": []}, "approval")

    assert config == {}


def test_get_step_config_returns_empty_for_missing_steps_key():
    """A snapshot with no 'steps' key returns empty dict without raising."""
    from app.services.workflow_engine import get_step_config

    config = get_step_config({}, "approval")

    assert config == {}


# ---------------------------------------------------------------------------
# VALID_TRANSITIONS — auto-approve paths must be legal
# ---------------------------------------------------------------------------


def test_valid_transition_pending_to_approved():
    """pending → approved is a required transition for auto-approve after extraction."""
    from app.models.invoice import InvoiceStatus
    from app.services.workflow_engine import VALID_TRANSITIONS

    assert InvoiceStatus.approved in VALID_TRANSITIONS[InvoiceStatus.pending]


def test_valid_transition_new_to_approved():
    """new → approved must be valid for workflows that skip the extraction step."""
    from app.models.invoice import InvoiceStatus
    from app.services.workflow_engine import VALID_TRANSITIONS

    assert InvoiceStatus.approved in VALID_TRANSITIONS[InvoiceStatus.new]


def test_approved_leads_to_erp_or_done():
    """After auto-approve the workflow must be able to advance to ERP or done."""
    from app.models.invoice import InvoiceStatus
    from app.services.workflow_engine import VALID_TRANSITIONS

    post_approved = VALID_TRANSITIONS[InvoiceStatus.approved]
    assert InvoiceStatus.sending_to_erp in post_approved
    assert InvoiceStatus.done in post_approved


# ---------------------------------------------------------------------------
# Auto-approve decision logic — isolated unit tests
# ---------------------------------------------------------------------------


def test_confidence_above_threshold_triggers_auto_approve():
    """When confidence >= threshold and auto_approve_enabled, target should be approved."""
    # Replicate the extraction.py decision logic in isolation
    overall_confidence = 0.97
    ext_cfg = {"auto_approve_enabled": True, "auto_approve_threshold": 0.95}

    auto_approved = False
    if ext_cfg.get("auto_approve_enabled") and overall_confidence >= ext_cfg.get(
        "auto_approve_threshold", 0.95
    ):
        auto_approved = True

    assert auto_approved is True


def test_confidence_below_threshold_does_not_auto_approve():
    """When confidence < threshold, auto-approve is not triggered."""
    overall_confidence = 0.80
    ext_cfg = {"auto_approve_enabled": True, "auto_approve_threshold": 0.95}

    auto_approved = False
    if ext_cfg.get("auto_approve_enabled") and overall_confidence >= ext_cfg.get(
        "auto_approve_threshold", 0.95
    ):
        auto_approved = True

    assert auto_approved is False


def test_auto_approve_disabled_skips_confidence_check():
    """Even with sufficient confidence, auto_approve_enabled=False prevents auto-approve."""
    overall_confidence = 0.99
    ext_cfg = {"auto_approve_enabled": False, "auto_approve_threshold": 0.95}

    auto_approved = False
    if ext_cfg.get("auto_approve_enabled") and overall_confidence >= ext_cfg.get(
        "auto_approve_threshold", 0.95
    ):
        auto_approved = True

    assert auto_approved is False


def test_amount_below_auto_approve_below_triggers_auto_approve():
    """When invoice amount is strictly below auto_approve_below, auto-approve activates."""
    invoice_amount = 499.99
    approval_cfg = {"auto_approve_below": 500.0}

    auto_approved = False
    auto_below = approval_cfg.get("auto_approve_below")
    if auto_below is not None and float(invoice_amount) < auto_below:
        auto_approved = True

    assert auto_approved is True


def test_amount_at_auto_approve_below_does_not_trigger():
    """Amount equal to auto_approve_below is NOT auto-approved (strictly less-than)."""
    invoice_amount = 500.0
    approval_cfg = {"auto_approve_below": 500.0}

    auto_approved = False
    auto_below = approval_cfg.get("auto_approve_below")
    if auto_below is not None and float(invoice_amount) < auto_below:
        auto_approved = True

    assert auto_approved is False


def test_amount_above_auto_approve_below_does_not_trigger():
    """Amount above auto_approve_below requires normal review."""
    invoice_amount = 1000.0
    approval_cfg = {"auto_approve_below": 500.0}

    auto_approved = False
    auto_below = approval_cfg.get("auto_approve_below")
    if auto_below is not None and float(invoice_amount) < auto_below:
        auto_approved = True

    assert auto_approved is False


def test_missing_auto_approve_below_does_not_trigger():
    """When auto_approve_below is absent from config, no amount-based auto-approve occurs."""
    invoice_amount = 1.0
    approval_cfg = {}  # key not present

    auto_approved = False
    auto_below = approval_cfg.get("auto_approve_below")
    if auto_below is not None and float(invoice_amount) < auto_below:
        auto_approved = True

    assert auto_approved is False


# ---------------------------------------------------------------------------
# decide_auto_approve — the real decision function, incl. money-control gates
# ---------------------------------------------------------------------------


def test_decide_auto_approve_confident_small_invoice():
    from decimal import Decimal

    from app.services.extraction import decide_auto_approve

    assert decide_auto_approve(
        {"auto_approve_enabled": True, "auto_approve_threshold": 0.95},
        {},
        overall_confidence=0.97,
        amount=Decimal("100"),
    )


def test_decide_auto_approve_below_floor():
    from decimal import Decimal

    from app.services.extraction import decide_auto_approve

    assert decide_auto_approve(
        {"auto_approve_enabled": False},
        {"auto_approve_below": 500},
        overall_confidence=0.1,
        amount=Decimal("100"),
    )


def test_decide_auto_approve_revoked_over_max_invoice_amount():
    """A confident extraction over `max_invoice_amount` must NOT auto-approve —
    the human path hard-rejects (422) at that cap; auto-approve mustn't slip past."""
    from decimal import Decimal

    from app.services.extraction import decide_auto_approve

    assert not decide_auto_approve(
        {"auto_approve_enabled": True, "auto_approve_threshold": 0.95},
        {"max_invoice_amount": 10000},
        overall_confidence=0.99,
        amount=Decimal("50000"),
    )


def test_decide_auto_approve_revoked_over_cfo_gate():
    """A confident extraction over `require_cfo_above` must NOT auto-approve —
    the 'system (auto-approve)' actor is not a CFO, so a human must decide."""
    from decimal import Decimal

    from app.services.extraction import decide_auto_approve

    assert not decide_auto_approve(
        {"auto_approve_enabled": True, "auto_approve_threshold": 0.95},
        {"require_cfo_above": 5000},
        overall_confidence=0.99,
        amount=Decimal("1000000"),
    )


def test_decide_auto_approve_at_cfo_gate_boundary_still_approves():
    """The CFO gate is strict greater-than (mirrors _enforce_approval_thresholds);
    an amount exactly at the threshold is not 'above' it."""
    from decimal import Decimal

    from app.services.extraction import decide_auto_approve

    assert decide_auto_approve(
        {"auto_approve_enabled": True, "auto_approve_threshold": 0.95},
        {"require_cfo_above": 5000},
        overall_confidence=0.99,
        amount=Decimal("5000"),
    )


def test_decide_auto_approve_not_triggered_stays_false():
    """No trigger (low confidence, no floor) → no auto-approve regardless of gates."""
    from decimal import Decimal

    from app.services.extraction import decide_auto_approve

    assert not decide_auto_approve(
        {"auto_approve_enabled": True, "auto_approve_threshold": 0.95},
        {"max_invoice_amount": 10000},
        overall_confidence=0.50,
        amount=Decimal("100"),
    )


@pytest.mark.parametrize("bad_threshold", ["abc", "10,000", "", {"x": 1}, "NaN", "Infinity"])
def test_decide_auto_approve_revoked_on_malformed_cfo_gate(bad_threshold):
    """A malformed `require_cfo_above` must REVOKE auto-approve (fall back to human
    review), never silently skip the gate and auto-approve a would-be CFO-gated
    invoice. Fail-closed at the auto-approve site too."""
    from decimal import Decimal

    from app.services.extraction import decide_auto_approve

    assert not decide_auto_approve(
        {"auto_approve_enabled": True, "auto_approve_threshold": 0.95},
        {"require_cfo_above": bad_threshold},
        overall_confidence=0.99,
        amount=Decimal("1000000"),
    )


# ---------------------------------------------------------------------------
# The other two thresholds must fail closed the same way. `decide_auto_approve`
# is a PURE function called from the extraction path — a raise there does not
# surface as a refusal, it lands the invoice in `failed`. Every one of these
# used to raise: `max_invoice_amount` and `auto_approve_below` on a bare
# `Decimal(str(...))`, and `auto_approve_threshold` on `0.99 >= "high"`
# (TypeError). `POST /api/workflows/import` takes `steps_config` free-form, so
# a non-numeric value can genuinely be persisted.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["abc", "10,000", "", {"x": 1}, [1], "NaN", "Infinity"])
def test_decide_auto_approve_revoked_on_malformed_max_amount(bad):
    from decimal import Decimal

    from app.services.extraction import decide_auto_approve

    assert not decide_auto_approve(
        {"auto_approve_enabled": True, "auto_approve_threshold": 0.95},
        {"max_invoice_amount": bad},
        overall_confidence=0.99,
        amount=Decimal("100"),
    )


@pytest.mark.parametrize("bad", ["abc", "1,000", "", {"x": 1}, [1], "NaN", "Infinity"])
def test_malformed_auto_approve_floor_is_not_a_floor(bad):
    """An unusable `auto_approve_below` must not raise AND must not trigger
    auto-approve — an amount is never 'below' a threshold nobody can read."""
    from decimal import Decimal

    from app.services.extraction import decide_auto_approve

    assert not decide_auto_approve(
        {"auto_approve_enabled": False},
        {"auto_approve_below": bad},
        overall_confidence=0.0,
        amount=Decimal("1"),
    )


@pytest.mark.parametrize("bad", ["high", "", None, {"x": 1}, [0.9], float("nan"), 95, -1])
def test_malformed_confidence_bar_disables_the_confidence_trigger(bad):
    """A non-numeric or out-of-range `auto_approve_threshold` must disable the
    confidence trigger rather than raise a TypeError out of a pure function."""
    from decimal import Decimal

    from app.services.extraction import decide_auto_approve

    assert not decide_auto_approve(
        {"auto_approve_enabled": True, "auto_approve_threshold": bad},
        {},
        overall_confidence=0.99,
        amount=Decimal("100"),
    )


def test_wellformed_thresholds_still_auto_approve():
    """Guard against over-refusing — every fail-closed branch above must fire
    only on a genuinely unusable value."""
    from decimal import Decimal

    from app.services.extraction import decide_auto_approve

    assert decide_auto_approve(
        {"auto_approve_enabled": True, "auto_approve_threshold": "0.90"},
        {"max_invoice_amount": "10000", "auto_approve_below": "500", "require_cfo_above": "9000"},
        overall_confidence=0.95,
        amount=Decimal("100"),
    )
