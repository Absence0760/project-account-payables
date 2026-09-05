"""The exception-agent resolvers must name the cap that actually refused them.

All four autonomous resolvers enforce two money caps before self-approving:
`max_invoice_amount` (a hard reject) and `require_cfo_above` (demands a human
CFO). Both are read through `approval_chain`'s shared fail-CLOSED body
(`_money_gate_applies`), so the VERDICT was always right — but every resolver
evaluated the max-amount cap through `cfo_gate_applies`, whose telemetry says
"auto-approval money threshold is unparseable (…); requiring human (CFO)
approval". An operator chasing a stuck agent was pointed at
`require_cfo_above` when the malformed value was `max_invoice_amount`, on the
one code path where the log is the only evidence of what happened.

This is a diagnosability fix, so these tests are about the message and the call
graph, not the verdict — the verdict is unchanged by construction (identical
shared body) and is already covered by `test_approval_thresholds.py`.

Both tests fail against the previous implementation: the AST guard finds four
`cfo_gate_applies(max_amount, …)` call sites, and the log assertion shows the
two helpers emit different, non-interchangeable diagnoses.
"""

from __future__ import annotations

import ast
import logging
import pathlib
from decimal import Decimal

import pytest

from app.services.approval_chain import cfo_gate_applies, max_amount_gate_applies

_RESOLVERS = pathlib.Path(__file__).resolve().parents[1] / "app/services/exception_agents/resolvers"
_FILES = ("amount_mismatch.py", "gl_coding.py", "missing_po.py", "multi_po_split.py")

# The local variable every resolver reads the hard cap into.
_MAX_CAP_VAR = "max_amount"


def _gate_calls(path: pathlib.Path) -> list[tuple[str, str | None]]:
    """`(gate_helper_name, first_arg_variable_name)` for every money-gate call."""
    tree = ast.parse(path.read_text())
    out: list[tuple[str, str | None]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id not in ("cfo_gate_applies", "max_amount_gate_applies"):
            continue
        first = node.args[0] if node.args else None
        out.append((node.func.id, first.id if isinstance(first, ast.Name) else None))
    return out


@pytest.mark.parametrize("filename", _FILES)
def test_resolver_evaluates_the_hard_cap_through_the_max_amount_helper(filename):
    calls = _gate_calls(_RESOLVERS / filename)
    assert calls, f"{filename} evaluates no money gate — did the control move?"

    # The hard cap is never routed through the CFO helper...
    assert (
        "cfo_gate_applies",
        _MAX_CAP_VAR,
    ) not in calls, (
        f"{filename} evaluates `{_MAX_CAP_VAR}` (the max_invoice_amount cap) through "
        "cfo_gate_applies, which logs 'requiring human (CFO) approval' for a cap trip "
        "that has nothing to do with the CFO threshold. Use max_amount_gate_applies."
    )
    # ...and the max-amount helper is used for exactly that, at least once.
    assert ("max_amount_gate_applies", _MAX_CAP_VAR) in calls, (
        f"{filename} no longer evaluates `{_MAX_CAP_VAR}` through max_amount_gate_applies"
    )
    # The max-amount helper is never borrowed for the CFO threshold either.
    for helper, arg in calls:
        if helper == "max_amount_gate_applies":
            assert arg == _MAX_CAP_VAR, (
                f"{filename} uses max_amount_gate_applies for `{arg}`, not the hard cap"
            )


def test_the_two_helpers_emit_different_diagnoses(caplog):
    """A malformed cap must be reported as the cap it is."""
    with caplog.at_level(logging.ERROR, logger="app.services.approval_chain"):
        assert max_amount_gate_applies("not-a-number", Decimal("1")) is True
    max_msgs = [r.getMessage() for r in caplog.records]
    assert any("max-invoice-amount" in m and "refusing the approval" in m for m in max_msgs), (
        max_msgs
    )
    assert not any("(CFO)" in m for m in max_msgs), max_msgs

    caplog.clear()
    with caplog.at_level(logging.ERROR, logger="app.services.approval_chain"):
        assert cfo_gate_applies("not-a-number", Decimal("1")) is True
    cfo_msgs = [r.getMessage() for r in caplog.records]
    assert any("requiring human (CFO) approval" in m for m in cfo_msgs), cfo_msgs
    assert not any("max-invoice-amount" in m for m in cfo_msgs), cfo_msgs
