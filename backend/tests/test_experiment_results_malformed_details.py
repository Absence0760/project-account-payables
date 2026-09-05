"""`GET /api/experiments/{id}/results` survives a non-object `audit_log.details`.

`details` is JSONB with **no object-shape constraint**. Every writer in this
codebase stores an object, so a list / string / number can only arrive by a
direct-DB write — which is exactly the tampering a control readout should
survive rather than be taken down by. `_experiment_metric_rows` called
`.get(...)` on the raw value in two places (the `ready_for_review` clock-start
scan and the terminal-decision row), so ONE such row raised `AttributeError` out
of the endpoint as a 500 and lost the whole experiment's evidence — every other
invoice in both arms included.

It now reads a non-object `details` as carrying nothing, matching
`services/approval_signature.check_approval_row`, which absorbs the same shape
by counting the row instead of failing the period. Deliberately nothing more:
the row is not re-interpreted as "corrections present", because a malformed blob
is not evidence that a human changed a field.

Both tests fail against the previous implementation with `AttributeError:
'list' object has no attribute 'get'`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.api.workflow_experiments import _details_obj, _experiment_metric_rows
from app.services.workflow_experiments import VARIANT_A, VARIANT_B, compute_experiment_results


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _StubSession:
    """Replays the four SELECTs `_experiment_metric_rows` issues, in order:
    decision rows, clock-start rows, invoice base rows, exception rows."""

    def __init__(self, *responses):
        self._responses = list(responses)

    async def execute(self, _q):
        return _Result(self._responses.pop(0) if self._responses else [])


_NOW = datetime.now(UTC)


@pytest.mark.asyncio
async def test_non_object_details_does_not_lose_the_whole_readout():
    tampered = uuid.uuid4()  # arm A — `details` is a JSON array
    healthy = uuid.uuid4()  # arm B — ordinary object `details`
    exp = SimpleNamespace(assignments={str(tampered): VARIANT_A, str(healthy): VARIANT_B})

    decision_rows = [
        (tampered, "invoice.approved", _NOW, ["not", "an", "object"]),
        (healthy, "invoice.approved", _NOW, {"changes": {"amount": {"old": "1", "new": "2"}}}),
    ]
    start_rows = [
        # A scalar `details` on the clock-start scan — the other pre-fix crash site.
        (tampered, _NOW - timedelta(days=2), "ready_for_review"),
        (healthy, _NOW - timedelta(days=2), {"new_status": "ready_for_review"}),
    ]
    inv_rows = [(tampered, _NOW - timedelta(days=3)), (healthy, _NOW - timedelta(days=3))]

    rows_a, rows_b = await _experiment_metric_rows(
        _StubSession(decision_rows, start_rows, inv_rows, []), exp
    )

    # The healthy arm's evidence is intact — the whole point.
    assert len(rows_b) == 1
    assert rows_b[0]["decision"] == "approved"
    assert rows_b[0]["unmodified"] is False  # real `details.changes`

    # The tampered row is still counted, carrying nothing rather than crashing.
    assert len(rows_a) == 1
    assert rows_a[0]["decision"] == "approved"
    assert rows_a[0]["unmodified"] is True  # no corrections recorded
    # Its scalar clock-start row was ignored, so the clock fell back to
    # `invoices.created_at` (3 days) instead of the `ready_for_review` row.
    assert rows_a[0]["time_to_approval_days"] is not None

    # And the readout itself still computes.
    results = compute_experiment_results(
        rows_a, rows_b, primary_metric="touchless_rate", min_sample_per_variant=1
    )
    assert results.variant_a.completed_count == 1
    assert results.variant_b.completed_count == 1


@pytest.mark.asyncio
async def test_a_single_bad_row_cannot_empty_the_other_arm():
    """Regression on the blast radius: pre-fix the exception escaped
    `_experiment_metric_rows` entirely, so arm B returned nothing at all."""
    bad = uuid.uuid4()
    good_1, good_2 = uuid.uuid4(), uuid.uuid4()
    exp = SimpleNamespace(
        assignments={str(bad): VARIANT_A, str(good_1): VARIANT_B, str(good_2): VARIANT_B}
    )

    decision_rows = [
        (bad, "invoice.rejected", _NOW, 42),  # scalar details
        (good_1, "invoice.auto_approved", _NOW, {}),
        (good_2, "invoice.rejected", _NOW, {}),
    ]
    rows_a, rows_b = await _experiment_metric_rows(_StubSession(decision_rows, [], [], []), exp)

    assert [r["decision"] for r in rows_a] == ["rejected"]
    assert sorted(r["decision"] for r in rows_b) == ["approved", "rejected"]
    assert [r["auto_approved"] for r in rows_b if r["decision"] == "approved"] == [True]


@pytest.mark.parametrize("value", [None, [], ["x"], "ready_for_review", 42, 1.5, True, ({"a": 1},)])
def test_details_obj_only_passes_through_objects(value):
    assert _details_obj(value) == {}


def test_details_obj_passes_an_object_through_unchanged():
    d = {"new_status": "ready_for_review"}
    assert _details_obj(d) is d
