"""Tests for the approval-routing additions:
  - department / GL / vendor routing rules on chain levels
  - parallel_mode "all" vs "any"
  - escalation: when a level sits past escalation_hours, the sweeper
    appends `escalation_to_user_ids` onto its `approver_ids` list

The sweeper itself talks to a tenant DB; that integration lives in the
e2e suite. These pin the pure-Python edges.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace


def _instance(state_data=None):
    return SimpleNamespace(id=uuid.uuid4(), state_data=state_data, state="active")


# ---------- routing rules ------------------------------------------------


def test_routing_rule_eq_match_includes_level():
    from app.services.approval_chain import resolve_applicable_levels

    chain = [
        {
            "name": "IT",
            "routing_rules": [{"field": "department", "operator": "eq", "value": "IT"}],
        }
    ]
    out = resolve_applicable_levels(chain, amount=1000, invoice_attrs={"department": "IT"})
    assert len(out) == 1


def test_routing_rule_eq_mismatch_excludes_level():
    from app.services.approval_chain import resolve_applicable_levels

    chain = [
        {
            "name": "IT",
            "routing_rules": [{"field": "department", "operator": "eq", "value": "IT"}],
        }
    ]
    out = resolve_applicable_levels(chain, amount=1000, invoice_attrs={"department": "Finance"})
    assert out == []


def test_routing_rule_in_set_match():
    from app.services.approval_chain import resolve_applicable_levels

    chain = [
        {
            "name": "Ops",
            "routing_rules": [
                {"field": "gl_account", "operator": "in", "value": ["6000", "6100", "6200"]}
            ],
        }
    ]
    out = resolve_applicable_levels(chain, amount=500, invoice_attrs={"gl_account": "6100"})
    assert len(out) == 1


def test_routing_rule_starts_with():
    from app.services.approval_chain import resolve_applicable_levels

    chain = [
        {
            "name": "OpEx",
            "routing_rules": [{"field": "gl_account", "operator": "starts_with", "value": "6"}],
        }
    ]
    out = resolve_applicable_levels(chain, amount=500, invoice_attrs={"gl_account": "6100"})
    assert len(out) == 1


def test_routing_rules_and_compose_with_amount():
    """Routing rules AND with min/max amount; both must hold."""
    from app.services.approval_chain import resolve_applicable_levels

    chain = [
        {
            "name": "IT-large",
            "min_amount": 1000,
            "routing_rules": [{"field": "department", "operator": "eq", "value": "IT"}],
        }
    ]
    # Right dept, amount too low → excluded.
    assert resolve_applicable_levels(chain, amount=500, invoice_attrs={"department": "IT"}) == []
    # Right dept, amount fine → included.
    assert (
        len(resolve_applicable_levels(chain, amount=2000, invoice_attrs={"department": "IT"})) == 1
    )


def test_routing_unknown_field_silently_passes():
    """A stale UI config that references a field the engine doesn't know
    must not hard-fail — the unknown field reads as None and only
    matches `ne`/`not_in` rules. This is the fail-open design."""
    from app.services.approval_chain import resolve_applicable_levels

    chain = [
        {
            "name": "X",
            "routing_rules": [{"field": "made_up_field", "operator": "eq", "value": "x"}],
        }
    ]
    # No invoice has `made_up_field`, so eq fails → level excluded.
    assert resolve_applicable_levels(chain, amount=1, invoice_attrs={}) == []


def test_invoice_routing_attrs_picks_off_invoice():
    from app.services.approval_chain import invoice_routing_attrs

    vendor_id = uuid.uuid4()
    inv = SimpleNamespace(
        gl_account="6100",
        cost_center="CC-1",
        department="Eng",
        vendor_id=vendor_id,
    )
    attrs = invoice_routing_attrs(inv)
    assert attrs["gl_account"] == "6100"
    assert attrs["cost_center"] == "CC-1"
    assert attrs["department"] == "Eng"
    assert attrs["vendor_id"] == str(vendor_id)


# ---------- parallel_mode -------------------------------------------------


def test_parallel_mode_any_satisfies_with_required_count():
    """Default `any`: required=2, two distinct approvers → complete."""
    from app.services.approval_chain import advance_approval_chain, init_chain_state

    inst = _instance()
    init_chain_state(
        inst,
        [
            {
                "name": "L",
                "approver_ids": ["a", "b", "c"],
                "required_approvals": 2,
                "parallel_mode": "any",
            }
        ],
    )
    a, b = uuid.uuid4(), uuid.uuid4()
    assert advance_approval_chain(inst, a) is False
    assert advance_approval_chain(inst, b) is True


def test_parallel_mode_all_requires_every_listed_approver():
    """`all` mode: every approver_id must approve, regardless of required_approvals."""
    from app.services.approval_chain import advance_approval_chain, init_chain_state

    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    inst = _instance()
    init_chain_state(
        inst,
        [
            {
                "name": "L",
                "approver_ids": [str(a), str(b), str(c)],
                "required_approvals": 1,  # ignored in `all` mode
                "parallel_mode": "all",
            }
        ],
    )
    assert advance_approval_chain(inst, a) is False
    assert advance_approval_chain(inst, b) is False
    assert advance_approval_chain(inst, c) is True


def test_parallel_mode_all_ignores_duplicate_approvals():
    """Approving twice from the same user does not double-count."""
    from app.services.approval_chain import advance_approval_chain, init_chain_state

    a, b = uuid.uuid4(), uuid.uuid4()
    inst = _instance()
    init_chain_state(
        inst,
        [
            {
                "name": "L",
                "approver_ids": [str(a), str(b)],
                "parallel_mode": "all",
            }
        ],
    )
    advance_approval_chain(inst, a)
    advance_approval_chain(inst, a)  # duplicate
    # Still missing b, so the level isn't satisfied.
    assert inst.state_data["approval_levels"]["current_level"] == 0


# ---------- escalation ---------------------------------------------------


def test_init_chain_state_stamps_entered_at_on_first_level():
    from app.services.approval_chain import init_chain_state

    inst = _instance()
    init_chain_state(inst, [{"name": "L", "approver_ids": []}])
    levels = inst.state_data["approval_levels"]["levels"]
    assert levels[0]["entered_at"] is not None


def test_advance_stamps_entered_at_on_next_level():
    """When the chain advances past a level, the new current level gets
    its own entered_at timestamp so the sweeper can age it."""
    from app.services.approval_chain import advance_approval_chain, init_chain_state

    inst = _instance()
    init_chain_state(
        inst,
        [
            {"name": "L1", "approver_ids": [], "required_approvals": 1},
            {"name": "L2", "approver_ids": [], "required_approvals": 1},
        ],
    )
    advance_approval_chain(inst, uuid.uuid4())
    levels = inst.state_data["approval_levels"]["levels"]
    assert levels[1]["entered_at"] is not None


def test_apply_escalation_no_op_when_not_due():
    from app.services.approval_chain import apply_escalation, init_chain_state

    inst = _instance()
    init_chain_state(
        inst,
        [
            {
                "name": "L",
                "approver_ids": ["a"],
                "escalation_hours": 4,
                "escalation_to_user_ids": ["esc-1"],
            }
        ],
    )
    # entered_at is now → not yet due.
    changed = apply_escalation(inst)
    assert changed is False


def test_apply_escalation_appends_targets_when_overdue():
    from app.services.approval_chain import apply_escalation, init_chain_state

    inst = _instance()
    init_chain_state(
        inst,
        [
            {
                "name": "L",
                "approver_ids": ["a"],
                "escalation_hours": 4,
                "escalation_to_user_ids": ["esc-1", "esc-2"],
            }
        ],
    )
    # Backdate entry so the sweep sees it as overdue.
    levels = inst.state_data["approval_levels"]["levels"]
    levels[0]["entered_at"] = (datetime.now(UTC) - timedelta(hours=5)).isoformat()
    inst.state_data = inst.state_data  # nudge

    changed = apply_escalation(inst)
    assert changed is True
    after = inst.state_data["approval_levels"]["levels"][0]
    assert "esc-1" in after["approver_ids"]
    assert "esc-2" in after["approver_ids"]
    assert len(after["escalations"]) == 1
    assert after["escalations"][0]["after_hours"] == 4


def test_apply_escalation_idempotent():
    """Once a level has absorbed the escalation user set, re-running is a
    no-op — the sweeper can run on a tight interval without spamming the
    state. New escalation targets WOULD trigger a fresh event, but a
    redundant call must not."""
    from app.services.approval_chain import apply_escalation, init_chain_state

    inst = _instance()
    init_chain_state(
        inst,
        [
            {
                "name": "L",
                "approver_ids": ["a"],
                "escalation_hours": 4,
                "escalation_to_user_ids": ["esc-1"],
            }
        ],
    )
    levels = inst.state_data["approval_levels"]["levels"]
    levels[0]["entered_at"] = (datetime.now(UTC) - timedelta(hours=5)).isoformat()
    inst.state_data = inst.state_data

    assert apply_escalation(inst) is True
    assert apply_escalation(inst) is False  # second call is no-op


def test_apply_escalation_skips_levels_without_config():
    from app.services.approval_chain import apply_escalation, init_chain_state

    inst = _instance()
    init_chain_state(inst, [{"name": "L", "approver_ids": ["a"]}])
    # No escalation_hours / escalation_to_user_ids → never escalates.
    levels = inst.state_data["approval_levels"]["levels"]
    levels[0]["entered_at"] = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    inst.state_data = inst.state_data
    assert apply_escalation(inst) is False
