"""Dry-run simulation of a no-code workflow against a sample invoice context.

Pure and side-effect-free: given a ``steps_config`` envelope and an evaluation
``ctx`` (the dict produced by ``workflow_builder.build_invoice_context``), walk
the steps the same way the engine would and return a ``SimulationResult``-shaped
dict — the ordered path taken, the terminal state reached, and any warnings.

- ``condition`` steps are evaluated via ``workflow_builder.evaluate_condition``;
  the resulting ``goto`` (when set) reroutes the walk to that step ``number``.
- ``parallel`` steps are described via ``workflow_builder.resolve_parallel``.
- ``webhook`` / ``email`` / ``delay`` steps go through
  ``workflow_builder.execute_custom_step(..., dry_run=True)`` so no side effect
  ever fires (no network, no email send, no sleep).
- ``extraction`` / ``approval`` / ``erp_export`` / ``done`` are described
  inline.

A loop guard caps the walk so a mis-wired condition ``goto`` cycle can't hang —
it records a warning and stops instead.
"""

from __future__ import annotations

from app.services.workflow_builder import (
    evaluate_condition,
    execute_custom_step,
    resolve_parallel,
)

# Hard ceiling on visited steps — protects against a condition goto cycle.
_MAX_VISITS = 200

_TERMINAL_BY_LAST_TYPE = {
    "erp_export": "sent_to_erp",
    "approval": "approved",
    "done": "done",
}


def _describe_static(step: dict) -> tuple[str, str]:
    """outcome, detail for the non-builder canonical step types."""
    step_type = step.get("type")
    name = step.get("name") or step_type
    if step_type == "extraction":
        cfg = step.get("config") or {}
        if cfg.get("auto_approve_enabled"):
            thr = cfg.get("auto_approve_threshold", 0.95)
            return "extracted", f"Extract fields; auto-approve above {thr} confidence."
        return "extracted", "Extract invoice fields for review."
    if step_type == "approval":
        cfg = step.get("config") or {}
        strategy = cfg.get("approver_strategy", "manual")
        return "approval_required", f"Route for approval (strategy: {strategy})."
    if step_type == "erp_export":
        return "exported", "Send the approved invoice to the ERP."
    if step_type == "done":
        return "done", "Workflow complete."
    return "executed", f"Step '{name}' executed."


async def simulate(steps_config: dict, ctx: dict) -> dict:
    """Simulate a walk through ``steps_config`` for the given context.

    Returns a dict matching ``schemas.workflow.SimulationResult``:
    ``{"path": [...], "terminal_state": str, "warnings": [str]}``.

    Async because ``workflow_builder.execute_custom_step`` is a coroutine (its
    email leg awaits the email adapter even in ``dry_run``). No side effect ever
    fires under ``dry_run=True``.
    """
    steps = (steps_config or {}).get("steps", []) or []
    by_number: dict[int, dict] = {}
    warnings: list[str] = []
    for s in steps:
        num = s.get("number")
        if num is None:
            warnings.append(
                f"Step '{s.get('name', '?')}' has no number; it cannot be a goto target."
            )
            continue
        if num in by_number:
            warnings.append(f"Duplicate step number {num}; the later definition wins.")
        by_number[num] = s

    ordered = [s for s in steps if s.get("number") is not None]
    ordered.sort(key=lambda s: s["number"])

    path: list[dict] = []
    if not ordered:
        return {"path": [], "terminal_state": "new", "warnings": warnings or ["No steps to run."]}

    # Walk by following the ordered list, but allow a condition step to jump.
    order_index = {s["number"]: i for i, s in enumerate(ordered)}
    idx = 0
    visits = 0
    last_executed_type = None

    while 0 <= idx < len(ordered):
        visits += 1
        if visits > _MAX_VISITS:
            warnings.append("Maximum step count exceeded — possible condition goto loop; stopping.")
            break

        step = ordered[idx]
        step_type = step.get("type")
        step_number = step["number"]
        name = step.get("name") or step_type

        if not step.get("enabled", True):
            path.append(
                {
                    "step_number": step_number,
                    "type": step_type,
                    "name": name,
                    "outcome": "skipped",
                    "detail": "Step is disabled.",
                }
            )
            idx += 1
            continue

        cfg = step.get("config") or {}

        if step_type == "condition":
            result = evaluate_condition(cfg, ctx)
            matched = result.get("matched", False)
            goto = result.get("goto")
            detail = result.get("explanation", "")
            path.append(
                {
                    "step_number": step_number,
                    "type": step_type,
                    "name": name,
                    "outcome": "matched" if matched else "not_matched",
                    "detail": detail,
                }
            )
            if goto is not None:
                if goto in order_index:
                    idx = order_index[goto]
                    continue
                warnings.append(
                    f"Condition step {step_number} points to unknown step {goto}; falling through."
                )
            idx += 1
            continue

        if step_type == "parallel":
            resolved = resolve_parallel(cfg)
            branch_names = [b.get("name", "") for b in resolved.get("branches", [])]
            required = resolved.get("required")
            join = resolved.get("join")
            detail = (
                f"Fan out to {len(branch_names)} branch(es) "
                f"[{', '.join(n for n in branch_names if n) or '—'}]; "
                f"join={join}, required={required}."
            )
            path.append(
                {
                    "step_number": step_number,
                    "type": step_type,
                    "name": name,
                    "outcome": "approval_required",
                    "detail": detail,
                }
            )
            last_executed_type = "approval"
            idx += 1
            continue

        if step_type in ("webhook", "email", "delay"):
            res = await execute_custom_step(step, ctx, dry_run=True)
            path.append(
                {
                    "step_number": step_number,
                    "type": step_type,
                    "name": name,
                    "outcome": res.get("status", "ok"),
                    "detail": res.get("detail", ""),
                }
            )
            last_executed_type = step_type
            idx += 1
            continue

        outcome, detail = _describe_static(step)
        path.append(
            {
                "step_number": step_number,
                "type": step_type,
                "name": name,
                "outcome": outcome,
                "detail": detail,
            }
        )
        last_executed_type = step_type
        idx += 1

    terminal_state = _TERMINAL_BY_LAST_TYPE.get(last_executed_type, "new")
    return {"path": path, "terminal_state": terminal_state, "warnings": warnings}
