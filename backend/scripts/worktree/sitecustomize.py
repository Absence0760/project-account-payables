"""Make an editable install follow the checkout you are running *from*.

`pip install -e ".[dev]"` writes an absolute mapping into the venv:
`site-packages/__editable___backend_0_1_0_finder.py` holds
`MAPPING = {'app': '<the checkout pip was run in>/backend/app'}` and installs
a `MetaPathFinder` that answers `import app` with that path. `backend/.venv`
does not carry into a git worktree, so a worktree reusing the primary venv
inherits that baked-in mapping.

That is fine right up until `<worktree>/backend` is missing from `sys.path`,
which happens more often than it sounds:

    cd <worktree>/backend && uvicorn app.main:app      # sys.path[0] = .../venv/bin
    cd <worktree>/backend && pytest --import-mode=importlib   # no prepend at all

Two branches of that class fixed themselves, because we own the code:
`backend/alembic.ini` gained `prepend_sys_path = %(here)s`, and every
`scripts/*.py` that imports `app` anchors its own checkout in its import
prologue (globbed by `tests/test_script_checkout_anchor.py`, so a new one
cannot forget). Neither needs activation. This shim is the general answer for
the rest — above all the console scripts we cannot edit.

`PathFinder` then finds nothing, the editable finder answers, and the command
runs the **primary checkout's** code while every message on screen names the
worktree. Nothing errors; the work is simply measured against a tree that was
never edited.

Contrary to a claim that has been written down twice in this repo, `PYTHONPATH`
*can* override that finder: setuptools **appends** it to `sys.meta_path`
(`sys.meta_path.append(_EditableFinder)`), so it lands *after* `PathFinder` and
anything genuinely on `sys.path` wins. The failure is a fall-through, not a
precedence fight — which is why `pytest` and `python main.py` happen to be safe
(pytest's rootdir insert and the script's own directory put `<worktree>/backend`
on `sys.path` first) while `python scripts/seed.py` is not.

So this shim does two things, and only when it can *prove* they are needed:

1. put the checkout you are running from on `sys.path` (fixing the
   fall-through at its source), and
2. drop the `__editable__*` finder, so a name it could not fix fails loudly
   with `ModuleNotFoundError` instead of quietly resolving elsewhere.

It is a **no-op inside the checkout the editable install points at** — the
common case, and the one where firing would be worse than not existing. It is
also a no-op when it cannot work out which checkout you are in.

Activation (opt-in, per command — nothing changes until you ask for it):

    PYTHONPATH=<repo>/backend/scripts/worktree python scripts/seed.py

`sitecustomize` is auto-imported by `site` only when it is importable at
interpreter startup, and `sys.path[0]` (the script's directory) is not set that
early — `PYTHONPATH` is the mechanism that works. Confirm it took effect:

    python -c "import sitecustomize, app; print(sitecustomize.__file__, app.__file__)"

or ask this file directly, which reports what it sees and would do:

    python backend/scripts/worktree/sitecustomize.py

Scope: the `__editable__*` MetaPathFinder strategy, which is what
`pip install -e` produces for this project. Because the file is named
`sitecustomize.py` it shadows any other `sitecustomize` further along
`sys.path`; opt in per command rather than exporting `PYTHONPATH` for a whole
shell if you have one of your own.

See `frontend/tests-e2e/README.md` § Running from a worktree.
"""

from __future__ import annotations

import os
import sys

_EDITABLE_PREFIX = "__editable__"

# How far up from an anchor directory to look for a sibling checkout, and how
# many trailing components of the install-time package root to try re-rooting.
# Both are small on purpose: this runs on every interpreter start.
_MAX_ANCESTORS = 12
_MAX_TAIL_PARTS = 3

# Returned when an anchor proves we are inside the checkout the editable
# install already points at — the signal to leave everything alone. Not an
# absolute path, so it can never collide with a resolved candidate.
SAME_CHECKOUT = "same-checkout"


def _real(path: str) -> str:
    return os.path.realpath(os.path.abspath(path))


def editable_mapping(finder: object) -> dict[str, str] | None:
    """Return an `__editable__*` finder's top-level name -> path map, else None.

    Both the class setuptools appends and any instance of it carry the
    generated module's name on `__module__`; everything else on `sys.meta_path`
    (importlib's own finders, virtualenv's, pytest's rewrite hook) is ignored.
    """
    module_name = getattr(finder, "__module__", "") or ""
    if not module_name.startswith(_EDITABLE_PREFIX):
        return None
    module = sys.modules.get(module_name)
    mapping = getattr(module, "MAPPING", None)
    if not isinstance(mapping, dict) or not mapping:
        return None
    try:
        return {str(name): _real(str(path)) for name, path in mapping.items()}
    except (TypeError, ValueError):
        return None


def install_root(mapping: dict[str, str]) -> str | None:
    """The directory the mapped packages live in, or None if they disagree.

    `{'app': '/repo/backend/app'}` -> `/repo/backend`. A mapping whose entries
    sit in different directories has no single root to re-point, so we decline
    rather than guess.
    """
    roots = {os.path.dirname(path) for path in mapping.values()}
    if len(roots) != 1:
        return None
    return roots.pop()


def _holds_all(root: str, names: list[str]) -> bool:
    for name in names:
        base = os.path.join(root, name)
        if os.path.isfile(base + ".py") or os.path.isfile(os.path.join(base, "__init__.py")):
            continue
        return False
    return True


def _tails(root: str) -> list[str]:
    """`''`, then the last 1..N components of `root`, shortest first.

    Anchored at `<worktree>/backend` the empty tail matches; anchored at the
    worktree's repo root, `backend` does. Shortest-first keeps the nearest
    plausible checkout winning over a farther one.
    """
    parts = [part for part in root.split(os.sep) if part]
    tails = [""]
    for count in range(1, min(_MAX_TAIL_PARTS, len(parts)) + 1):
        tails.append(os.path.join(*parts[len(parts) - count :]))
    return tails


def resolve_from_anchor(anchor: str, mapping: dict[str, str]) -> str | None:
    """Which package root should serve `mapping` for something run in `anchor`?

    Returns the local package root when it is provably a *different* checkout,
    `SAME_CHECKOUT` when the anchor sits inside the one the install points at,
    and None when neither can be established.
    """
    baked_root = install_root(mapping)
    if baked_root is None:
        return None
    names = sorted(mapping)
    tails = _tails(baked_root)
    # Only accept a candidate laid out like the install-time root: if pip was
    # run beside a pyproject.toml, so must the replacement be. Cheap, and it
    # keeps an unrelated tree that happens to contain an `app/` out.
    want_pyproject = os.path.isfile(os.path.join(baked_root, "pyproject.toml"))

    directory = _real(anchor)
    for _ in range(_MAX_ANCESTORS):
        for tail in tails:
            candidate = _real(os.path.join(directory, tail)) if tail else directory
            if candidate == baked_root:
                return SAME_CHECKOUT
            if not _holds_all(candidate, names):
                continue
            if want_pyproject and not os.path.isfile(os.path.join(candidate, "pyproject.toml")):
                continue
            return candidate
        parent = os.path.dirname(directory)
        if parent == directory:
            break
        directory = parent
    return None


def default_anchors(argv: list[str] | None = None, cwd: str | None = None) -> list[str]:
    """Where to look from, most trustworthy first.

    The working directory leads: it is what the developer typed, and it is the
    one anchor a console script cannot mislead. `sys.argv[0]`'s directory is a
    useful fallback for `python <elsewhere>/backend/scripts/seed.py`, but for a
    console script it is the *venv's* `bin/`, which sits inside the primary
    checkout and would read as "same checkout" — hence second, and consulted
    only when the working directory settles nothing.
    """
    anchors = [os.getcwd() if cwd is None else cwd]
    argv = sys.argv if argv is None else argv
    if argv:
        script = argv[0]
        if script and os.path.isfile(script):
            anchors.append(os.path.dirname(os.path.abspath(script)))
    seen: set[str] = set()
    unique = []
    for anchor in anchors:
        real = _real(anchor)
        if real not in seen:
            seen.add(real)
            unique.append(real)
    return unique


def resolve(mapping: dict[str, str], anchors: list[str]) -> str | None:
    """The local package root to use, or None to leave the finder alone."""
    for anchor in anchors:
        result = resolve_from_anchor(anchor, mapping)
        if result == SAME_CHECKOUT:
            return None
        if result:
            return result
    return None


def install(
    meta_path: list | None = None,
    path: list[str] | None = None,
    anchors: list[str] | None = None,
) -> list[tuple[object, str]]:
    """Re-point every provably-stale `__editable__*` finder. Returns what changed."""
    meta_path = sys.meta_path if meta_path is None else meta_path
    path = sys.path if path is None else path
    anchors = default_anchors() if anchors is None else anchors

    changed: list[tuple[object, str]] = []
    for finder in list(meta_path):
        mapping = editable_mapping(finder)
        if mapping is None:
            continue
        local_root = resolve(mapping, anchors)
        if local_root is None:
            continue
        if local_root not in path:
            path.insert(0, local_root)
        meta_path.remove(finder)
        changed.append((finder, local_root))
    return changed


def _report() -> int:
    already = sys.modules.get("sitecustomize")
    if getattr(already, "__file__", None) == __file__:
        print("note: this shim is already active via PYTHONPATH, so it has")
        print("      already re-pointed/removed anything it was going to.")
    anchors = default_anchors()
    print(f"anchors: {anchors}")
    found = False
    for finder in list(sys.meta_path):
        mapping = editable_mapping(finder)
        if mapping is None:
            continue
        found = True
        print(f"editable finder: {getattr(finder, '__module__', finder)}")
        print(f"  maps to: {mapping}")
        local_root = resolve(mapping, anchors)
        if local_root is None:
            print("  verdict: leave alone (same checkout, or no local checkout found)")
        else:
            print(f"  verdict: re-point to {local_root} and drop the finder")
    if not found:
        print("no __editable__* finder on sys.meta_path — nothing for this shim to do")
        print("(run this with the venv's python: <repo>/backend/.venv/bin/python)")
    return 0


if __name__ == "sitecustomize":  # pragma: no cover - interpreter startup path
    # Never let a bug here break every python process that opts in.
    try:
        install()
    except Exception:  # noqa: BLE001 - startup hook, failing open is the point
        pass
elif __name__ == "__main__":
    raise SystemExit(_report())
