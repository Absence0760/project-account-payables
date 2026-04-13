"""Smoke tests — verify the app can be imported and basic models work."""

from app.models.invoice import Invoice, InvoiceStatus


def test_invoice_status_values():
    """All expected invoice statuses exist."""
    expected = {
        "new",
        "pending",
        "ready_for_review",
        "approved",
        "rejected",
        "sending_to_erp",
        "sent_to_erp",
        "posted_in_erp",
        "payment_scheduled",
        "paid",
        "done",
        "failed",
    }
    actual = {s.value for s in InvoiceStatus}
    assert actual == expected


def test_invoice_model_has_required_columns():
    """Invoice model has the columns the API depends on."""
    columns = {c.name for c in Invoice.__table__.columns}
    required = {
        "id",
        "invoice_number",
        "vendor_name",
        "amount",
        "status",
        "created_at",
    }
    assert required.issubset(columns)


def test_valid_transitions_defined():
    """Workflow engine valid transitions cover core workflow statuses."""
    from app.services.workflow_engine import VALID_TRANSITIONS

    # Core workflow statuses managed by the workflow engine
    # (posted_in_erp, payment_scheduled, paid are managed by payment flow)
    core_statuses = {
        InvoiceStatus.new,
        InvoiceStatus.pending,
        InvoiceStatus.ready_for_review,
        InvoiceStatus.approved,
        InvoiceStatus.rejected,
        InvoiceStatus.sending_to_erp,
        InvoiceStatus.sent_to_erp,
        InvoiceStatus.failed,
        InvoiceStatus.done,
    }
    for status in core_statuses:
        assert status in VALID_TRANSITIONS, f"Missing transitions for {status}"
