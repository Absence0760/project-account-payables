"""Pydantic v2 schemas for quality inspections (4-way matching leg)."""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field

# The set of acceptable QualityInspection.result values. The API validates the
# request's ``result`` against this in the handler so a bad value is a clean 400
# (a Pydantic parse-time validator would surface as a 422 before the handler).
VALID_RESULTS = {"pass", "fail", "partial"}


class InspectionCreate(BaseModel):
    inspection_number: str = Field(..., max_length=100)
    po_id: str | None = None
    gr_id: str | None = None
    result: str = "pass"
    inspected_date: date | None = None
    inspector: str | None = Field(default=None, max_length=255)
    accepted_quantity: Decimal | None = Field(default=None, ge=0)
    rejected_quantity: Decimal | None = Field(default=None, ge=0)
    deviation_notes: str | None = None


# Responses are hand-serialized via ``app/api/inspections.py::_serialize`` (same
# dict-returning convention as ``app/api/goods_receipts.py``), so there's no
# response-model schema here — adding one risked silent drift from the serializer.
