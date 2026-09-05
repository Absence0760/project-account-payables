"""Touchless (straight-through-processing) rate — the POPULATION, not the
arithmetic.

`compute_touchless_rate` answers "how much work did the machine do instead of
a person?", so its numerator is a claim about invoices that PASSED REVIEW
untouched. Terminal status alone does not establish that:

  * `new -> done` is a legal `VALID_TRANSITIONS` edge that skips approval
    outright, and
  * the Day-0 CSV importer (`services/csv_import`) plants historical rows
    straight at `done` (its default) or `paid` with the workflow engine never
    running at all.

Both used to land in the numerator purely on their status, inflating a
board-reported automation figure off invoices nobody ever approved. The
numerator now requires positive evidence — the durable `Invoice.approval_date`
stamp — and an invoice with no such evidence is in NEITHER leg, exactly as an
extraction-failed invoice already was.

The DENOMINATOR had the mirror of that hole, and it is not fixable by the same
mechanism: an imported `rejected` row sits in the bounced leg as though a
reviewer HERE sent it back, and no approval stamp can ever exclude it because
nothing writes one on a rejection. Provenance settles it instead — the
importer stamps `meta["imported"]` on every row it creates, and marked rows
leave BOTH legs. An UNMARKED row is native by definition, because the marker
is only ever written going forward.

The arithmetic guards (rejected in the denominator, never negative, zero-safe)
live in `test_dashboard_aggregations.py`; the end-to-end realdb guards live in
`test_dashboard_aggregates.py`.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.services.analytics import (
    TOUCHLESS_BOUNCED_STATUSES,
    TOUCHLESS_CLEARED_STATUSES,
    TOUCHLESS_REVIEW_EVIDENCE_STATUSES,
    compute_touchless_rate,
)
from app.services.workflow_engine import VALID_TRANSITIONS

# ---------------------------------------------------------------------------
# The numerator's population
# ---------------------------------------------------------------------------


def test_done_without_an_approval_stamp_is_not_in_the_numerator():
    """`new -> done` skips approval entirely. That invoice did not clear
    review — it bypassed it — so it must not be counted as touchless.

    One untouched approval (`approved`) + one `new -> done` shortcut. Under
    the old status-only rule the shortcut counted as cleared and the rate read
    100%; both legs saw it, so it was invisible. It is now out of the
    population and the honest rate is still 100% — off ONE invoice, not two.
    """
    pipeline = {"approved": 1, "done": 1}
    assert compute_touchless_rate(pipeline, review_cleared_count=0, imported_pipeline={}) == 100.0

    # And it genuinely left the numerator: pair it with a rejection and the
    # figure is 1/2, not the 2/3 the old wider population produced.
    assert (
        compute_touchless_rate(
            {**pipeline, "rejected": 1}, review_cleared_count=0, imported_pipeline={}
        )
        == 50.0
    )


def test_done_reached_through_a_genuine_untouched_approval_is_in_the_numerator():
    """The other `done`: approved (auto or by a reviewer who changed nothing),
    then carried through to terminal. It carries `approval_date`, so it counts
    exactly as it always did."""
    pipeline = {"done": 1, "rejected": 1}
    assert compute_touchless_rate(pipeline, review_cleared_count=1, imported_pipeline={}) == 50.0


def test_a_mixed_tenant_counts_only_the_evidenced_done_invoices():
    """Nine CSV-imported historical `done` rows and one genuinely approved one,
    against a single rejection. Old rule: 10/11 = 90.9%. New rule: 1/2 = 50%.
    """
    pipeline = {"done": 10, "rejected": 1}
    assert compute_touchless_rate(pipeline, review_cleared_count=1, imported_pipeline={}) == 50.0


def test_paid_is_evidence_gated_too_because_csv_import_can_plant_it():
    """`services/csv_import._IMPORTABLE_INVOICE_STATUSES` allows `paid`, so a
    `paid` row is not proof of review either — same hole as `done`, same fix.
    """
    assert "paid" in TOUCHLESS_REVIEW_EVIDENCE_STATUSES
    # One imported `paid` (no stamp) + one real approval + one rejection.
    pipeline = {"paid": 1, "approved": 1, "rejected": 1}
    assert compute_touchless_rate(pipeline, review_cleared_count=0, imported_pipeline={}) == 50.0
    # Same shape, but the `paid` one really was approved → 2/3.
    assert compute_touchless_rate(pipeline, review_cleared_count=1, imported_pipeline={}) == 66.7


# ---------------------------------------------------------------------------
# What did NOT change
# ---------------------------------------------------------------------------


def test_a_human_touched_invoice_is_excluded_exactly_as_before():
    """A rejection is a human touching the invoice: denominator only, never
    the numerator. Unchanged by the population edit."""
    assert TOUCHLESS_BOUNCED_STATUSES == ("rejected",)
    assert (
        compute_touchless_rate({"rejected": 4}, review_cleared_count=0, imported_pipeline={}) == 0.0
    )
    assert (
        compute_touchless_rate(
            {"approved": 3, "rejected": 1}, review_cleared_count=0, imported_pipeline={}
        )
        == 75.0
    )


def test_the_unambiguous_cleared_statuses_need_no_evidence():
    """Every `VALID_TRANSITIONS` edge into these originates at `approved`, so
    status IS the proof — they must not be evidence-gated (that would silently
    zero the metric for every tenant mid-pipeline)."""
    for status in TOUCHLESS_CLEARED_STATUSES:
        assert (
            compute_touchless_rate({status: 1}, review_cleared_count=0, imported_pipeline={})
            == 100.0
        )


def test_denominator_only_loses_invoices_that_never_reached_review():
    """The edit moves the numerator's population; the denominator changes ONLY
    by the same never-reviewed rows leaving it. Nothing that finished review
    is dropped, and nothing new is added.

    Baseline: 3 evidenced-cleared + 1 rejected = 4 reviewed. Adding five
    never-reviewed rows (`new`, `pending`, `ready_for_review`, an unstamped
    `done`, an extraction-`failed`) leaves the rate untouched at 3/4.
    """
    base = {"approved": 3, "rejected": 1}
    assert compute_touchless_rate(base, review_cleared_count=0, imported_pipeline={}) == 75.0

    with_noise = {
        **base,
        "new": 2,
        "pending": 3,
        "ready_for_review": 1,
        "done": 1,
        "failed": 1,
    }
    assert compute_touchless_rate(with_noise, review_cleared_count=0, imported_pipeline={}) == 75.0


def test_extraction_failed_still_counts_in_neither_leg():
    """The pre-existing `failed` split is preserved verbatim: a stamped
    `failed` (approved, then the ERP export blew up) is in both legs, an
    extraction failure in neither."""
    assert (
        compute_touchless_rate(
            {"failed": 1, "approved": 1}, review_cleared_count=0, imported_pipeline={}
        )
        == 100.0
    )
    assert (
        compute_touchless_rate(
            {"failed": 1, "rejected": 1}, review_cleared_count=1, imported_pipeline={}
        )
        == 50.0
    )


def test_no_invoices_finished_review_is_zero_not_a_zero_division():
    assert (
        compute_touchless_rate(
            {"new": 5, "pending": 2}, review_cleared_count=0, imported_pipeline={}
        )
        == 0.0
    )
    assert compute_touchless_rate({}, review_cleared_count=0, imported_pipeline={}) == 0.0


# ---------------------------------------------------------------------------
# Structural guards — the sets have to keep saying what they claim
# ---------------------------------------------------------------------------


def test_the_evidence_count_can_never_exceed_the_invoices_it_describes():
    """A caller passing a count larger than the ambiguous population (a stale
    or unrelated figure) must not push the rate above 100 or invent invoices.
    """
    assert (
        compute_touchless_rate(
            {"done": 1, "rejected": 1}, review_cleared_count=99, imported_pipeline={}
        )
        == 50.0
    )
    assert (
        compute_touchless_rate({"rejected": 1}, review_cleared_count=99, imported_pipeline={})
        == 0.0
    )
    assert (
        compute_touchless_rate({"approved": 1}, review_cleared_count=-5, imported_pipeline={})
        == 100.0
    )


def test_the_caller_must_state_the_evidence():
    """`review_cleared_count` is required, so a caller that has not been
    updated fails loudly instead of quietly re-publishing the old, wider
    number."""
    with pytest.raises(TypeError):
        compute_touchless_rate({"approved": 1})  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        compute_touchless_rate({"approved": 1}, imported_pipeline={})  # type: ignore[call-arg]


def test_the_caller_must_state_the_provenance():
    """`imported_pipeline` is required for the same reason: a caller still on
    the old signature must raise rather than silently publish a rate whose
    denominator is padded with somebody else's migrated history."""
    with pytest.raises(TypeError):
        compute_touchless_rate({"approved": 1}, review_cleared_count=0)  # type: ignore[call-arg]


def test_the_three_legs_are_disjoint():
    legs = [
        set(TOUCHLESS_CLEARED_STATUSES),
        set(TOUCHLESS_REVIEW_EVIDENCE_STATUSES),
        set(TOUCHLESS_BOUNCED_STATUSES),
    ]
    for i, a in enumerate(legs):
        for b in legs[i + 1 :]:
            assert not (a & b)


def test_no_pre_review_status_has_an_edge_into_the_unconditionally_cleared_set():
    """The comment above `TOUCHLESS_CLEARED_STATUSES` claims those statuses are
    proof of a cleared review. Re-derive that from the state machine rather
    than trusting the comment.

    `approved` is the set's single entry point — every writer of it
    (`services/review`, `api/workflow`'s below-threshold auto-approve,
    `services/extraction`'s auto-approve) stamps `Invoice.approval_date`, and
    a CSV import is explicitly forbidden from landing there. So no status that
    sits BEFORE review (`new`, `pending`, `ready_for_review`, `rejected`) may
    have an edge into the set other than into `approved` itself; such an edge
    would be a way in that never passed approval, and its target would belong
    in the evidence-gated set instead.

    Sources that are themselves evidence-gated are skipped: `failed ->
    sending_to_erp` is the ERP-export retry, which presupposes the approval
    that got the invoice to `sending_to_erp` in the first place — a runtime
    property the graph cannot express, and exactly why `failed` needs the
    stamp rather than a graph rule.
    """
    cleared = set(TOUCHLESS_CLEARED_STATUSES)
    assert "approved" in cleared
    exempt_sources = cleared | set(TOUCHLESS_REVIEW_EVIDENCE_STATUSES)
    checked = 0
    for source, targets in VALID_TRANSITIONS.items():
        source_name = source.value if hasattr(source, "value") else str(source)
        if source_name in exempt_sources:
            continue
        checked += 1
        for target in targets:
            target_name = target.value if hasattr(target, "value") else str(target)
            if target_name == "approved":
                # The auto-approve edges (`new`/`pending` -> `approved`) ARE a
                # cleared review — that is precisely what touchless means.
                continue
            assert target_name not in cleared, (
                f"{source_name} -> {target_name} reaches a status listed as "
                "unconditionally cleared without passing through approval"
            )
    assert checked, "the pre-review statuses vanished from VALID_TRANSITIONS"


def test_every_status_the_csv_importer_can_plant_is_handled():
    """A CSV import bypasses the workflow engine entirely, so any status it can
    land at is un-evidenced by construction. Each must be either
    evidence-gated or outside the metric — never unconditionally cleared."""
    from app.services.csv_import import _IMPORTABLE_INVOICE_STATUSES

    for status in _IMPORTABLE_INVOICE_STATUSES:
        assert status not in TOUCHLESS_CLEARED_STATUSES, (
            f"{status!r} is CSV-importable, so status alone cannot prove it cleared review"
        )


# ---------------------------------------------------------------------------
# Provenance — imported rows are outside BOTH legs
# ---------------------------------------------------------------------------


def test_an_imported_rejection_is_in_neither_leg():
    """The denominator's half of the problem. A migrated historical `rejected`
    row is not a reviewer here bouncing an invoice back — the workflow engine
    never ran on it — so it must not deflate the rate.

    One genuine untouched approval + one imported rejection. Before this
    change the imported rejection padded the denominator: 1/2 = 50%. It is now
    out of the population entirely and the honest rate is 100%, off ONE
    invoice.
    """
    pipeline = {"approved": 1, "rejected": 1}
    assert compute_touchless_rate(pipeline, review_cleared_count=0, imported_pipeline={}) == 50.0
    assert (
        compute_touchless_rate(pipeline, review_cleared_count=0, imported_pipeline={"rejected": 1})
        == 100.0
    )


def test_an_imported_done_row_leaves_the_denominator_too():
    """An imported `done` row was ALREADY out of the numerator (it carries no
    approval stamp). Confirm it is now out of the denominator as well, rather
    than being parked in the un-evidenced middle where a later change could
    quietly start counting it.
    """
    pipeline = {"approved": 1, "done": 1}
    # Un-evidenced either way, so the rate reads the same...
    assert compute_touchless_rate(pipeline, review_cleared_count=0, imported_pipeline={}) == 100.0
    assert (
        compute_touchless_rate(pipeline, review_cleared_count=0, imported_pipeline={"done": 1})
        == 100.0
    )
    # ...but the row's absence is observable: it can no longer be promoted into
    # the numerator by an evidence count, because the native population in
    # `done` is now zero.
    assert (
        compute_touchless_rate(
            {"done": 1, "rejected": 1}, review_cleared_count=1, imported_pipeline={}
        )
        == 50.0
    )
    assert (
        compute_touchless_rate(
            {"done": 1, "rejected": 1}, review_cleared_count=1, imported_pipeline={"done": 1}
        )
        == 0.0
    )


def test_a_native_rejection_still_counts_exactly_as_before():
    """The bounced leg is otherwise untouched — only PROVABLY imported rows
    leave it. A tenant that never imports sees no change at all."""
    for count in (1, 4, 10):
        assert (
            compute_touchless_rate(
                {"approved": count, "rejected": count},
                review_cleared_count=0,
                imported_pipeline={},
            )
            == 50.0
        )
    # A marker on a DIFFERENT status never touches the rejections.
    assert (
        compute_touchless_rate(
            {"approved": 1, "rejected": 1, "done": 3},
            review_cleared_count=0,
            imported_pipeline={"done": 3},
        )
        == 50.0
    )


def test_an_unmarked_legacy_row_is_treated_as_native():
    """No marker means "we do not know", and the marker is written only going
    forward — so a row imported before it shipped stays in the population. The
    empty `imported_pipeline` IS that case, and it must reproduce the previous
    behaviour exactly."""
    pipeline = {"approved": 2, "done": 3, "rejected": 1}
    assert compute_touchless_rate(pipeline, review_cleared_count=1, imported_pipeline={}) == 75.0
    assert compute_touchless_rate(pipeline, review_cleared_count=1, imported_pipeline={}) == (
        compute_touchless_rate(pipeline, review_cleared_count=1, imported_pipeline={"new": 9})
    )


def test_a_wholly_imported_tenant_reports_zero_not_a_fabricated_rate():
    """Day 0: ten thousand migrated rows and nothing processed here yet. Every
    leg is empty, so the rate is the zero-safe 0.0 — the same answer a brand
    new tenant gets, which is the honest one."""
    pipeline = {"done": 9_000, "paid": 900, "rejected": 100}
    assert (
        compute_touchless_rate(pipeline, review_cleared_count=0, imported_pipeline=dict(pipeline))
        == 0.0
    )


def test_provenance_counts_are_clamped_per_status():
    """A stale or unrelated `imported_pipeline` must not drive a leg negative,
    and a surplus in one status must not cancel real rows in another."""
    # More imported `rejected` claimed than exist — the leg floors at zero
    # rather than subtracting from the cleared leg.
    assert (
        compute_touchless_rate(
            {"approved": 1, "rejected": 1},
            review_cleared_count=0,
            imported_pipeline={"rejected": 99},
        )
        == 100.0
    )
    assert (
        compute_touchless_rate(
            {"rejected": 2}, review_cleared_count=0, imported_pipeline={"rejected": 99}
        )
        == 0.0
    )
    # None / junk values are treated as zero, never crash the dashboard.
    assert (
        compute_touchless_rate(
            {"approved": 1, "rejected": 1},
            review_cleared_count=0,
            imported_pipeline={"rejected": None},
        )
        == 50.0
    )
    assert (
        compute_touchless_rate({"approved": 1}, review_cleared_count=0, imported_pipeline=None)  # type: ignore[arg-type]
        == 100.0
    )


def test_the_importer_marks_every_invoice_row_it_creates():
    """The metric's exclusion is only as good as the marker. Guard the two
    halves of the contract that live in `services/csv_import`: the reserved
    key, and the fact that the marker records WHAT wrote it and WHEN.
    """
    from app.services.csv_import import (
        IMPORT_PROVENANCE_KEY,
        IMPORT_PROVENANCE_SOURCE,
        build_import_provenance,
    )

    assert IMPORT_PROVENANCE_KEY == "imported"
    # Must not collide with the other tenants of `Invoice.meta`.
    assert IMPORT_PROVENANCE_KEY not in {"audit_summary", "archived_at"}
    marker = build_import_provenance()
    assert marker["source"] == IMPORT_PROVENANCE_SOURCE
    # An ISO-8601 instant, timezone-aware (UTC), round-trippable.
    parsed = datetime.fromisoformat(marker["at"])
    assert parsed.tzinfo is not None
