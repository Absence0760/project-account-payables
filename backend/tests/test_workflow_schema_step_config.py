"""`WorkflowStepConfig.config` must resolve against the model its sibling
`type` field actually names, not whichever Union member the bare-union
resolution happens to score highest.

Before the fix, `config` was an undiscriminated `Union[ExtractionStepConfig |
ApprovalStepConfig | ErpExportStepConfig | ... | dict]` with every member's
fields optional and the last member a catch-all `dict`. Pydantic's default
"smart mode" union resolution then had no reliable signal to pick the right
variant:

  1. Saving the real `erp_export` step config `{"erp_system": "default"}`
     silently coerced into `ExtractionStepConfig`'s shape — total data loss,
     no validation error.
  2. A full, valid `ApprovalStepConfig` payload (including the Decimal money
     fields `auto_approve_below` / `require_cfo_above` / `max_invoice_amount`)
     resolved to the untyped `dict` fallback — the Union's own Decimal
     typing was never actually enforced on this write path.

Found by exploratory persona-driven testing (approver persona), noted
incidentally while configuring an unrelated max-amount test. Filed as #237.

Pure schema tests — no DB, no HTTP.
"""

from __future__ import annotations

from decimal import Decimal

from app.schemas.workflow import (
    ApprovalStepConfig,
    ErpExportStepConfig,
    ExtractionStepConfig,
    WorkflowStepConfig,
)


def test_erp_export_config_resolves_to_erp_export_step_config():
    step = WorkflowStepConfig.model_validate(
        {
            "number": 3,
            "type": "erp_export",
            "name": "Send to ERP",
            "config": {"erp_system": "default"},
        }
    )
    assert isinstance(step.config, ErpExportStepConfig)
    assert not isinstance(step.config, ExtractionStepConfig)


def test_full_approval_config_resolves_to_approval_step_config_not_dict():
    step = WorkflowStepConfig.model_validate(
        {
            "number": 2,
            "type": "approval",
            "name": "Approval",
            "config": {
                "required": True,
                "auto_approve_below": "500.00",
                "require_cfo_above": "10000.00",
                "max_invoice_amount": "50000.00",
                "require_segregation": True,
            },
        }
    )
    assert isinstance(step.config, ApprovalStepConfig)
    # Money fields are Decimal — the Union's own typing, previously bypassed
    # by the dict fallback, is now actually enforced on this write path.
    assert step.config.auto_approve_below == Decimal("500.00")
    assert isinstance(step.config.auto_approve_below, Decimal)
    assert step.config.require_cfo_above == Decimal("10000.00")
    assert step.config.max_invoice_amount == Decimal("50000.00")


def test_extraction_config_resolves_to_extraction_step_config():
    step = WorkflowStepConfig.model_validate(
        {
            "number": 1,
            "type": "extraction",
            "name": "Extraction",
            "config": {"auto_approve_enabled": True, "auto_approve_threshold": 0.9},
        }
    )
    assert isinstance(step.config, ExtractionStepConfig)
    assert step.config.auto_approve_enabled is True
    assert step.config.auto_approve_threshold == 0.9


def test_done_step_has_no_dedicated_config_model_and_falls_through():
    """ "done" has no config shape of its own — the dispatch table
    deliberately omits it, so it falls through to the Union's own
    resolution (an empty dict stays a dict, the harmless pre-existing
    behavior for a step type with nothing to type)."""
    step = WorkflowStepConfig.model_validate(
        {"number": 4, "type": "done", "name": "Done", "config": {}}
    )
    assert isinstance(step.config, dict)


def test_unrecognized_extra_fields_on_a_typed_step_do_not_corrupt_the_model():
    """An extra/unknown key inside a recognized step's config must not throw
    off which model it resolves to — it's dropped (Pydantic's default
    extra="ignore"), not silently misrouted to a different step type."""
    step = WorkflowStepConfig.model_validate(
        {
            "number": 2,
            "type": "approval",
            "name": "Approval",
            "config": {"required": False, "some_future_field": "unused"},
        }
    )
    assert isinstance(step.config, ApprovalStepConfig)
    assert step.config.required is False


def test_default_factory_dict_config_still_works_when_config_omitted():
    """A step with no `config` key at all must still validate — the dispatch
    only intercepts when `config` is present as a dict, so an omitted
    `config` falls through untouched to the field's own
    `default_factory=dict`, same as before this fix."""
    step = WorkflowStepConfig.model_validate({"number": 1, "type": "approval", "name": "Approval"})
    assert step.config == {}
