"""Pre-built workflow templates for the no-code builder.

Static, in-code (local-first — no DB, no network). Each template is a plain
dict matching the ``WorkflowTemplate`` schema shape:
``{key, name, description, category, steps_config}`` where ``steps_config`` is
the canonical ``{"steps": [ <step>, ... ]}`` envelope. The step ``config`` keys
match reviews/workflow-builder-spec.md exactly (the same keys Worker A's
``workflow_builder`` evaluates).

``POST /api/workflows/from-template`` clones a template's ``steps_config`` into
a fresh, inactive ``WorkflowDefinition``.
"""

from __future__ import annotations

# NOTE: keep step `number`s 1-based and contiguous within each template. The
# `condition` step's `*_goto` targets reference another step's `number`.

_TEMPLATES: list[dict] = [
    {
        "key": "simple_approval",
        "name": "Simple approval",
        "description": (
            "Extract the invoice, route it to a single manager approval, then "
            "export to the ERP. The starting point for most teams."
        ),
        "category": "standard",
        "steps_config": {
            "steps": [
                {
                    "number": 1,
                    "type": "extraction",
                    "name": "Data extraction",
                    "enabled": True,
                    "config": {
                        "auto_approve_enabled": False,
                        "auto_approve_threshold": 0.95,
                    },
                },
                {
                    "number": 2,
                    "type": "approval",
                    "name": "Manager approval",
                    "enabled": True,
                    "config": {"required": True, "approver_strategy": "manual"},
                },
                {
                    "number": 3,
                    "type": "erp_export",
                    "name": "Send to ERP",
                    "enabled": True,
                    "config": {"export_format": "json", "auto_send_on_approval": True},
                },
            ]
        },
    },
    {
        "key": "high_value_cfo_routing",
        "name": "High-value CFO routing",
        "description": (
            "Invoices at or above 10,000 are routed to the CFO; everything "
            "below goes through standard manager approval. Uses a condition "
            "step to branch on amount."
        ),
        "category": "routing",
        "steps_config": {
            "steps": [
                {
                    "number": 1,
                    "type": "extraction",
                    "name": "Data extraction",
                    "enabled": True,
                    "config": {
                        "auto_approve_enabled": False,
                        "auto_approve_threshold": 0.95,
                    },
                },
                {
                    "number": 2,
                    "type": "condition",
                    "name": "High value?",
                    "enabled": True,
                    "config": {
                        "rules": [{"field": "amount", "operator": "gte", "value": 10000}],
                        "match": "all",
                        # >= 10k → CFO sign-off (step 4); else manager (step 3)
                        "on_true_goto": 4,
                        "on_false_goto": 3,
                    },
                },
                {
                    "number": 3,
                    "type": "approval",
                    "name": "Manager approval",
                    "enabled": True,
                    "config": {"required": True, "approver_strategy": "manual"},
                },
                {
                    "number": 4,
                    "type": "approval",
                    "name": "CFO sign-off",
                    "enabled": True,
                    "config": {"required": True, "approver_strategy": "manual"},
                },
                {
                    "number": 5,
                    "type": "erp_export",
                    "name": "Send to ERP",
                    "enabled": True,
                    "config": {"export_format": "json", "auto_send_on_approval": True},
                },
            ]
        },
    },
    {
        "key": "parallel_approvers",
        "name": "Parallel approvers",
        "description": (
            "Fan out to two approval branches (finance + department head) at "
            "once and join when both have signed off."
        ),
        "category": "approval",
        "steps_config": {
            "steps": [
                {
                    "number": 1,
                    "type": "extraction",
                    "name": "Data extraction",
                    "enabled": True,
                    "config": {
                        "auto_approve_enabled": False,
                        "auto_approve_threshold": 0.95,
                    },
                },
                {
                    "number": 2,
                    "type": "parallel",
                    "name": "Dual approval",
                    "enabled": True,
                    "config": {
                        "branches": [
                            {"name": "Finance", "approver_ids": []},
                            {"name": "Department head", "approver_ids": []},
                        ],
                        "join": "all",
                        "min_approvals": None,
                    },
                },
                {
                    "number": 3,
                    "type": "erp_export",
                    "name": "Send to ERP",
                    "enabled": True,
                    "config": {"export_format": "json", "auto_send_on_approval": True},
                },
            ]
        },
    },
    {
        "key": "auto_approve_small",
        "name": "Auto-approve small invoices",
        "description": (
            "Invoices under 500 skip approval and go straight to the ERP; "
            "larger ones require a manager. Reduces clerk toil on low-risk spend."
        ),
        "category": "automation",
        "steps_config": {
            "steps": [
                {
                    "number": 1,
                    "type": "extraction",
                    "name": "Data extraction",
                    "enabled": True,
                    "config": {
                        "auto_approve_enabled": False,
                        "auto_approve_threshold": 0.95,
                    },
                },
                {
                    "number": 2,
                    "type": "condition",
                    "name": "Small invoice?",
                    "enabled": True,
                    "config": {
                        "rules": [{"field": "amount", "operator": "lt", "value": 500}],
                        "match": "all",
                        # < 500 → straight to ERP (step 4); else approval (step 3)
                        "on_true_goto": 4,
                        "on_false_goto": 3,
                    },
                },
                {
                    "number": 3,
                    "type": "approval",
                    "name": "Manager approval",
                    "enabled": True,
                    "config": {"required": True, "approver_strategy": "manual"},
                },
                {
                    "number": 4,
                    "type": "erp_export",
                    "name": "Send to ERP",
                    "enabled": True,
                    "config": {"export_format": "json", "auto_send_on_approval": True},
                },
            ]
        },
    },
    {
        "key": "webhook_email_notify",
        "name": "Webhook + email notify",
        "description": (
            "After approval, ping an external system via webhook and email the "
            "vendor that their invoice was approved, then export to the ERP."
        ),
        "category": "integration",
        "steps_config": {
            "steps": [
                {
                    "number": 1,
                    "type": "extraction",
                    "name": "Data extraction",
                    "enabled": True,
                    "config": {
                        "auto_approve_enabled": False,
                        "auto_approve_threshold": 0.95,
                    },
                },
                {
                    "number": 2,
                    "type": "approval",
                    "name": "Manager approval",
                    "enabled": True,
                    "config": {"required": True, "approver_strategy": "manual"},
                },
                {
                    "number": 3,
                    "type": "webhook",
                    "name": "Notify external system",
                    "enabled": True,
                    "config": {
                        "url": "https://example.invalid/hooks/invoice-approved",
                        "method": "POST",
                        "headers": {},
                        "body_template": '{"invoice": "{{invoice_number}}"}',
                        "timeout_seconds": 10,
                    },
                },
                {
                    "number": 4,
                    "type": "email",
                    "name": "Email vendor",
                    "enabled": True,
                    "config": {
                        "to": "vendor",
                        "to_addresses": [],
                        "subject": "Your invoice was approved",
                        "body_template": "Your invoice has been approved and is being processed.",
                    },
                },
                {
                    "number": 5,
                    "type": "erp_export",
                    "name": "Send to ERP",
                    "enabled": True,
                    "config": {"export_format": "json", "auto_send_on_approval": True},
                },
            ]
        },
    },
]

_BY_KEY: dict[str, dict] = {t["key"]: t for t in _TEMPLATES}


def list_templates() -> list[dict]:
    """Return all pre-built templates (defensive deep-ish copy of the list)."""
    return [dict(t) for t in _TEMPLATES]


def get_template(key: str) -> dict | None:
    """Return one template by key, or ``None`` if unknown."""
    return _BY_KEY.get(key)
