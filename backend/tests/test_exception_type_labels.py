"""Guard: every real exception type must have a friendly label.

`app/api/exceptions.py::EXCEPTION_TYPE_LABELS` maps `Exception.exception_type`
values to the human-readable strings the exception-queue UI shows
(`type_label` in `_exception_dict`). A missing entry isn't a crash — `.get()`
falls back to the raw enum string — so it silently renders as e.g.
`quality_hold` instead of "Quality Hold" (issue #151). Pure-Python, no DB.

There's no single Enum/Literal for exception_type in this codebase (the
column is a plain `String(50)`); the real types are the string literals the
generating services actually emit. Two of them have a named module constant
we can import directly so those two can't silently drift; the rest are
asserted against the literals `invoice_warnings.py` emits (and the
documented set in `backend/CLAUDE.md` § Exception types).
"""

from __future__ import annotations

from app.api.erp_webhook import ERP_RECONCILIATION_EXCEPTION_TYPE
from app.api.exceptions import EXCEPTION_TYPE_LABELS
from app.services.contract_compliance import COMPLIANCE_EXCEPTION_TYPE

# Literal exception_type strings emitted by invoice_warnings.py that have no
# named constant of their own.
_LITERAL_EXCEPTION_TYPES = {
    "duplicate",
    "po_mismatch",
    "fraud_flag",
    "extraction_failed",
    "unverified_vendor",
    "review_rejected",
    "amount_exceeded",
    "missing_data",
    "quality_hold",
}

ALL_EXCEPTION_TYPES = _LITERAL_EXCEPTION_TYPES | {
    COMPLIANCE_EXCEPTION_TYPE,
    ERP_RECONCILIATION_EXCEPTION_TYPE,
}


def test_every_exception_type_has_a_label():
    missing = ALL_EXCEPTION_TYPES - EXCEPTION_TYPE_LABELS.keys()
    assert not missing, f"EXCEPTION_TYPE_LABELS is missing entries for: {sorted(missing)}"


def test_labels_are_non_empty_human_readable_strings():
    for exc_type, label in EXCEPTION_TYPE_LABELS.items():
        assert isinstance(label, str) and label.strip(), (
            f"EXCEPTION_TYPE_LABELS[{exc_type!r}] must be a non-empty string"
        )
        # The whole point of the map is to not show the raw snake_case value.
        assert "_" not in label, (
            f"EXCEPTION_TYPE_LABELS[{exc_type!r}] = {label!r} looks like a raw "
            "enum value, not a friendly label"
        )
