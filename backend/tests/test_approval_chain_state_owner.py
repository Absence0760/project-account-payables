"""`approval_chain` owns the `approval_levels` JSONB key, and stays that way.

The multi-level approval chain lives in one JSONB slot —
``WorkflowInstance.state_data["approval_levels"]`` — and eight sites across
three services used to reach into it by hand, in **two spellings**:

    state.get("approval_levels", {})        # approval_chain ×2
    state.get("approval_levels") or {}      # review ×2, approval_escalation ×1

Those two are not synonyms. They differ on exactly one input — the key present
holding JSON ``null`` — where the first returns ``None`` and the second returns
``{}``. Every consumer immediately calls ``.get("levels", ...)`` on the result,
so the divergence is not a routing decision but an ``AttributeError`` on the
approval path. The ``.get(key, {})`` sites only ever survived because they
happened to test the result for truthiness before subscripting it: correct by
accident of call order, one reordered line from a 500 on every approval of that
invoice.

``chain_state_of`` / ``get_chain_progress`` / ``clear_chain_state`` +
``CHAIN_STATE_KEY`` are the single owner. ``get_chain_progress`` already
existed with **no caller at all** and carried the wrong spelling of the two, so
it was reshaped rather than adopted as-is: an unused function is not evidence
of the right abstraction. It gained the raw-mapping form the two in-place
mutators need (they read the chain out of a deep COPY of ``state_data``, not
off the instance, and mutate the nested object they get back), the clear form
the reject path needs, and the key constant the writer and the SQL predicate
need.

The scans below are the drift guard, in the shape
``tests/test_utc_today.py`` and ``tests/test_payment_methods.py`` established:
an AST scan over `app/` for the string literal, plus the three self-tests that
stop a scan from passing because it is looking at nothing — the walk must visit
a floor of modules, the matcher must still flag a violation PLANTED in real app
source, and the owner must still spell the literal the scan searches for.

The scan is over the AST, not the text, so a comment explaining the key (there
is one in `api/invoices.py`, on why bulk-reject must clear it) is never an
offender — only code that names it.
"""

from __future__ import annotations

import ast
import pathlib
import uuid
from types import SimpleNamespace

import pytest

from app.services.approval_chain import (
    CHAIN_STATE_KEY,
    advance_approval_chain,
    apply_escalation,
    chain_state_of,
    clear_chain_state,
    get_chain_progress,
)

APP_DIR = pathlib.Path(__file__).resolve().parents[1] / "app"

#: The one module allowed to name the key.
CHAIN_STATE_OWNER = "services/approval_chain.py"

#: Floor on how many modules the `app/` scan must actually walk. The tree holds
#: several hundred; the number exists so a scan that silently stops finding
#: files (a moved `APP_DIR`, a glob typo, a package nested one level deeper)
#: fails as a broken guard instead of passing as a clean tree.
MIN_APP_MODULES = 400


def _instance(state_data):
    """A minimal WorkflowInstance-like row. `get_chain_progress` reads exactly
    one attribute, so a namespace is the honest fixture (and keeps this file
    DB-free), matching `tests/test_approval_chain.py`."""
    return SimpleNamespace(id=uuid.uuid4(), state_data=state_data)


# ---------------------------------------------------------------------------
# The owner's contract
# ---------------------------------------------------------------------------

#: Every shape `state_data` is reachable in, and the chain state each denotes.
#: The `null` row is the one the two legacy spellings disagreed on.
NO_CHAIN_STATE_DATA = [
    pytest.param(None, id="no-state_data"),
    pytest.param({}, id="empty-state_data"),
    pytest.param({"rejection_count": 2}, id="key-absent"),
    pytest.param({"approval_levels": None}, id="key-present-but-null"),
    pytest.param({"approval_levels": {}}, id="empty-chain"),
]


@pytest.mark.parametrize("state_data", NO_CHAIN_STATE_DATA)
def test_no_chain_reads_as_an_empty_dict(state_data):
    """A missing key, a stored `null` and an empty object all mean the same
    thing: this instance has no approval chain, and the next approval will
    initialise one. `{}` — never `None`, which is what the caller would then
    call `.get("levels")` on."""
    assert chain_state_of(state_data) == {}
    assert get_chain_progress(_instance(state_data)) == {}


def test_a_stored_null_is_the_only_place_the_two_legacy_spellings_diverged():
    """Pins the reason `or {}` is the correct spelling, rather than asserting it.

    `.get(key, {})` is the one that hands `None` to a caller that is about to
    subscript it. Both legacy spellings are reproduced here literally so the
    claim is checked, not restated in prose.
    """
    stored_null = {CHAIN_STATE_KEY: None}

    assert stored_null.get(CHAIN_STATE_KEY, {}) is None  # the spelling that broke
    assert (stored_null.get(CHAIN_STATE_KEY) or {}) == {}  # the spelling that did not
    assert chain_state_of(stored_null) == {}

    with pytest.raises(AttributeError):
        # What the `.get(key, {})` spelling would have done at any consumer
        # that reads `levels` without first testing the result for truthiness.
        stored_null.get(CHAIN_STATE_KEY, {}).get("levels", [])


@pytest.mark.parametrize(
    "state_data",
    [
        pytest.param(None, id="no-state_data"),
        pytest.param({}, id="empty-state_data"),
        pytest.param({"rejection_count": 2}, id="key-absent"),
        pytest.param({"approval_levels": {}}, id="empty-chain"),
        pytest.param({"approval_levels": {"levels": [], "current_level": 0}}, id="live-chain"),
        pytest.param({"approval_levels": [{"name": "Manager"}]}, id="malformed-list"),
        pytest.param({"approval_levels": "corrupt"}, id="malformed-string"),
    ],
)
def test_behaviour_is_unchanged_for_every_non_null_input(state_data):
    """The owner must be a pure consolidation everywhere except the `null` row.

    Both legacy spellings are re-evaluated here and compared against the owner,
    so this fails if the new reader is anything but a drop-in for non-`null`
    input — including for the two *malformed* shapes, which pass through
    untouched by design (see `chain_state_of`: corrupt state is counted as a
    failed instance by the escalation sweeper, and coercing it to `{}` would
    silently drop a real chain's requirement instead).
    """
    legacy_default = (state_data or {}).get(CHAIN_STATE_KEY, {})
    legacy_or = (state_data or {}).get(CHAIN_STATE_KEY) or {}

    assert chain_state_of(state_data) == legacy_default
    assert chain_state_of(state_data) == legacy_or
    assert get_chain_progress(_instance(state_data)) == legacy_default


def test_a_present_chain_is_returned_by_identity_so_mutators_can_write_through():
    """`advance_approval_chain` / `apply_escalation` read the chain out of a
    deep copy of `state_data` and mutate the nested object in place before
    reassigning the outer mapping. A reader that copied would make both of
    those silently no-op."""
    chain = {"levels": [], "current_level": 0}
    state = {CHAIN_STATE_KEY: chain}

    assert chain_state_of(state) is chain

    chain_state_of(state)["current_level"] = 3
    assert state[CHAIN_STATE_KEY]["current_level"] == 3


def test_clear_chain_state_removes_the_key_rather_than_nulling_it():
    """The reject path must leave no ambiguous `null` behind — that is the
    input this whole file exists because of. Idempotent: clearing an instance
    that never had a chain is a no-op, not a KeyError."""
    state = {"rejection_count": 1, CHAIN_STATE_KEY: {"levels": [], "current_level": 1}}
    clear_chain_state(state)
    assert CHAIN_STATE_KEY not in state
    assert state == {"rejection_count": 1}

    clear_chain_state(state)
    assert state == {"rejection_count": 1}


# ---------------------------------------------------------------------------
# The `null` case at the real consumers
# ---------------------------------------------------------------------------


def test_advance_approval_chain_treats_a_stored_null_as_no_chain():
    """`advance_approval_chain` returns "chain complete" for a chainless
    instance, so the approval proceeds as single-level rather than 500-ing."""
    instance = _instance({CHAIN_STATE_KEY: None})
    assert advance_approval_chain(instance, uuid.uuid4()) is True


def test_apply_escalation_treats_a_stored_null_as_nothing_to_escalate():
    instance = _instance({CHAIN_STATE_KEY: None})
    assert apply_escalation(instance) is False


def test_the_escalation_audit_detail_survives_a_stored_null():
    """`approval_escalation._last_escalation_detail` reads the chain to name
    the level it escalated. Under the `.get(key, {})` spelling a stored `null`
    would raise here — inside the sweeper's per-instance try, so it would have
    shown up as an unexplained failed-instance count, not as a bug report."""
    from app.services.approval_escalation import _last_escalation_detail

    assert _last_escalation_detail(_instance({CHAIN_STATE_KEY: None})) == {"level": 0}


# ---------------------------------------------------------------------------
# Drift guard: nothing under `app/` names the key but its owner
# ---------------------------------------------------------------------------


def chain_key_literal_lines(source: str, *, filename: str = "<test>") -> list[int]:
    """Line numbers of every string literal equal to the chain-state key.

    AST, not text: a comment or a docstring *mentioning* `approval_levels` is
    prose and is never an offender — which is what lets a module keep
    explaining the key it no longer reaches into by hand (`api/invoices.py`
    does exactly that). Only a literal the code actually evaluates counts,
    which is every spelling that matters: `.get("approval_levels")`,
    `state["approval_levels"]`, `.pop("approval_levels", None)`, and the
    SQLAlchemy JSONB path `state_data["approval_levels"].astext`.
    """
    tree = ast.parse(source, filename=filename)
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value == CHAIN_STATE_KEY
    ]


@pytest.mark.parametrize(
    "snippet",
    [
        'state.get("approval_levels", {})',
        'state.get("approval_levels") or {}',
        'state["approval_levels"] = {}',
        'state_data.pop("approval_levels", None)',
        "chain = state.get('approval_levels')",
        'WorkflowInstance.state_data["approval_levels"].astext.isnot(None)',
    ],
)
def test_scanner_catches_every_hand_rolled_spelling(snippet):
    """The scanner's own regression test — pin each of the six shapes the
    codebase actually used, so the guard cannot be quietly narrowed."""
    assert chain_key_literal_lines(snippet) == [1]


@pytest.mark.parametrize(
    "snippet",
    [
        "chain = get_chain_progress(instance)",
        "chain = chain_state_of(state)",
        "state[CHAIN_STATE_KEY] = {}",
        '# clears `state_data["approval_levels"]` so a rework re-runs the chain',
        '"""The chain lives under approval_levels on state_data."""',
        'state.get("approval_level")',
    ],
)
def test_scanner_ignores_the_owner_forms_and_prose(snippet):
    assert chain_key_literal_lines(snippet) == []


def test_no_module_under_app_names_the_chain_state_key():
    """Fails on any hand-rolled read/write of the `approval_levels` JSONB key.

    If this fires, go through `app/services/approval_chain.py` instead:
    `get_chain_progress(instance)` off a row, `chain_state_of(state_data)` off
    a raw mapping (or a deep copy you intend to mutate), `clear_chain_state`
    to remove it, `CHAIN_STATE_KEY` for a write or a SQL path. One owner is
    what makes the next schema move one edit — and what stops a sixth site
    picking the `.get(key, {})` spelling again.
    """
    offenders: list[str] = []
    for path in sorted(APP_DIR.rglob("*.py")):
        relative = str(path.relative_to(APP_DIR))
        if relative == CHAIN_STATE_OWNER:
            continue
        lines = chain_key_literal_lines(path.read_text(encoding="utf-8"), filename=str(path))
        offenders.extend(f"{relative}:{line}" for line in lines)

    assert not offenders, (
        f"the approval-chain JSONB key is named by hand at {offenders}. Use "
        "app/services/approval_chain.py — get_chain_progress / chain_state_of "
        "/ clear_chain_state / CHAIN_STATE_KEY."
    )


def test_the_owner_still_spells_the_key_exactly_once():
    """The scan searches for a literal, so it goes inert the moment that
    literal stops occurring — e.g. if `CHAIN_STATE_KEY` were rebuilt from
    parts, or the key renamed without re-pointing this file. The owner is the
    proof the needle still matches something, and it should hold the string
    once (the constant), not scattered through its own body."""
    owner = (APP_DIR / CHAIN_STATE_OWNER).read_text(encoding="utf-8")
    found = chain_key_literal_lines(owner, filename=CHAIN_STATE_OWNER)
    assert len(found) == 1, (
        f"{CHAIN_STATE_OWNER} spells the key at {found}; expected exactly one "
        "(the CHAIN_STATE_KEY constant). Zero means this scan now searches for "
        "a string that occurs nowhere and can never fail; more than one means "
        "the owner itself is hand-rolling the key again."
    )


def test_the_app_scan_actually_walks_the_tree():
    """First half of the vacuous-pass guard: the scan must SEE the code. "No
    offenders found" is also what an empty file list reports."""
    files = sorted(APP_DIR.rglob("*.py"))
    assert APP_DIR.is_dir(), f"{APP_DIR} is not a directory — the scan scans nothing"
    assert len(files) >= MIN_APP_MODULES, (
        f"only {len(files)} modules found under {APP_DIR} (expected >= "
        f"{MIN_APP_MODULES}) — the scan is reporting a clean tree because it is "
        "looking at almost none of it"
    )
    assert (APP_DIR / CHAIN_STATE_OWNER).is_file()


def test_the_app_scan_can_still_see_a_violation_in_real_app_source():
    """Second half: the scan must still RECOGNISE one where it runs — over a
    real several-hundred-line module parsed the same way, not just a one-line
    snippet. Nothing is written to disk; the planted source exists only in
    memory."""
    target = APP_DIR / "services" / "review.py"
    source = target.read_text(encoding="utf-8")
    assert chain_key_literal_lines(source, filename=str(target)) == [], (
        f"{target.name} already names the key — the plant below proves nothing"
    )

    planted = source + '\n\n_planted = (instance.state_data or {}).get("approval_levels")\n'
    found = chain_key_literal_lines(planted, filename=str(target))
    assert found == [len(planted.splitlines())], (
        "the scanner did not flag a planted hand-rolled read in real app source "
        f"(reported {found}) — the clean result above is not evidence"
    )
