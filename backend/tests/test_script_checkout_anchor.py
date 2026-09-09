"""Every `scripts/*.py` that imports `app` anchors its own checkout.

Run as `python scripts/<anything>.py`, `sys.path[0]` is `scripts/` — so `app`
is not on `sys.path` at all. A git worktree reusing the primary checkout's
`backend/.venv` then resolves it through the editable install's baked-in
`__editable___backend_0_1_0_finder`, which maps `app` to whichever checkout
`pip install -e` was run in. Measured, before the anchors:

    cd <worktree>/backend && python scripts/seed.py --help
    -> app.__file__ = <primary>/backend/app/__init__.py

`pnpm seed` and `pnpm migrate:all` are the first two commands a contributor
runs (step 4 of the root CLAUDE.md first-time setup), and both were silently
operating on another checkout's models while every line they printed named this
one. `scripts/worktree/sitecustomize.py` fixes the whole class, but only once
someone puts it on `PYTHONPATH`; these scripts are ours to edit, so each
carries the two-line anchor and needs no activation.

**The list is a glob, not a roll-call.** Naming the files would fix today's
instances and let the class reopen the next time someone adds a script — which
is the failure this file exists to prevent. The rule is derived instead: a
script that imports `app` must anchor; one that does not (`verify_tls.py`
today) needs nothing, and its exemption comes from that same derivation rather
than from an allowlist. `test_the_derivation_is_not_vacuous` is what stops a
broken glob turning "no scripts matched" into a green run.

Three properties, and the second is the one an innocent-looking edit breaks:

1. The anchor runs BEFORE the first `app` import — after it, it is decoration.
2. `scripts/` stays `sys.path[0]`. `seed.py` imports `seed_extras` bare, which
   only resolves because the script's own directory leads; the anchor adds the
   parent and must never displace that.
3. It is idempotent, and inert when the checkout is already on `sys.path` —
   the shim, `PYTHONPATH`, or simply being the checkout the venv was installed
   from. Running these scripts in the primary checkout must change nothing.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = BACKEND_ROOT / "scripts"

#: Locating the anchor is shared between these tests and the subprocess driver
#: below, so there is exactly one definition of "where the prologue ends".
_LOCATOR = '''
import ast


def anchor_index(tree):
    """Index of the `if <root> not in sys.path: sys.path.insert(...)` statement."""
    for i, node in enumerate(tree.body):
        if not isinstance(node, ast.If):
            continue
        for inner in ast.walk(node):
            if (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Attribute)
                and inner.func.attr == "insert"
                and isinstance(inner.func.value, ast.Attribute)
                and inner.func.value.attr == "path"
                and isinstance(inner.func.value.value, ast.Name)
                and inner.func.value.value.id == "sys"
            ):
                return i
    return -1
'''

_locator_ns: dict = {}
exec(compile(_LOCATOR, "<locator>", "exec"), _locator_ns)  # noqa: S102
anchor_index = _locator_ns["anchor_index"]


def _is_app_import(node: ast.AST) -> bool:
    if isinstance(node, ast.ImportFrom):
        return (node.module or "").split(".")[0] == "app"
    if isinstance(node, ast.Import):
        return any(alias.name.split(".")[0] == "app" for alias in node.names)
    return False


def _imports_app(tree: ast.Module) -> bool:
    """Anywhere, not only at module level.

    A function-level `from app... import ...` still resolves through `sys.path`
    when it runs, so it needs the anchor just as much — it merely fails later.
    """
    return any(_is_app_import(node) for node in ast.walk(tree))


def _first_top_level_app_import(tree: ast.Module) -> int:
    for i, node in enumerate(tree.body):
        if _is_app_import(node):
            return i
    return -1


ALL_SCRIPTS = sorted(SCRIPTS_DIR.glob("*.py"))
ANCHORED_SCRIPTS = [p for p in ALL_SCRIPTS if _imports_app(ast.parse(p.read_text("utf-8")))]
EXEMPT_SCRIPTS = [p for p in ALL_SCRIPTS if p not in ANCHORED_SCRIPTS]


# The driver execs ONLY the statements up to and including the anchor, so it
# needs nothing but the stdlib — which is what lets it run under `-S`.
_DRIVER = _LOCATOR + textwrap.dedent(
    """
    import importlib.util
    import json
    import sys

    script, times = sys.argv[1], int(sys.argv[2])
    preseed = sys.argv[3:]

    tree = ast.parse(open(script, encoding="utf-8").read())
    cut = anchor_index(tree) + 1
    code = compile(ast.Module(body=tree.body[:cut], type_ignores=[]), script, "exec")

    for entry in reversed(preseed):
        sys.path.insert(0, entry)
    before = list(sys.path)

    globals_ = {"__file__": script, "__name__": "anchor_prologue"}
    for _ in range(times):
        exec(code, globals_)

    out = {"path": list(sys.path), "before": before}
    try:
        import app

        out["app"] = app.__file__
    except Exception as exc:  # noqa: BLE001
        out["app"] = f"<{type(exc).__name__}>"
    spec = importlib.util.find_spec("seed_extras")
    out["seed_extras"] = spec.origin if spec else None
    print(json.dumps(out))
    """
)


def _run_prologue(script: Path, *, times: int = 1, preseed: list[str] | None = None) -> dict:
    """Execute a script's anchor prologue in a hostile-but-faithful interpreter.

    `-S` skips `site`, so no `.pth` runs and the editable finder does not exist;
    `-P` keeps the cwd off `sys.path`. What remains is the stdlib plus whatever
    we pre-seed — so `import app` can only succeed via the anchor, and it names
    the checkout it came from. Without `-S` the finder would answer and every
    assertion here would pass whether or not the anchor did anything.
    """
    preseed = [str(SCRIPTS_DIR)] if preseed is None else preseed
    result = subprocess.run(
        [sys.executable, "-S", "-P", "-c", _DRIVER, str(script), str(times), *preseed],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_the_derivation_is_not_vacuous() -> None:
    """A broken glob must fail loudly, not parametrize zero cases.

    Every assertion below is `@pytest.mark.parametrize`d over `ANCHORED_SCRIPTS`;
    an empty list would collect nothing and read as a pass, which is exactly the
    silent-green this file exists to prevent.
    """
    assert len(ALL_SCRIPTS) >= 13, ALL_SCRIPTS
    assert len(ANCHORED_SCRIPTS) >= 12, ANCHORED_SCRIPTS
    assert all(p.is_file() for p in ANCHORED_SCRIPTS)


@pytest.mark.parametrize("script", EXEMPT_SCRIPTS, ids=lambda p: p.name)
def test_an_exempt_script_is_exempt_because_it_never_imports_app(script: Path) -> None:
    """The exemption is derived, never an allowlist.

    A script that does not touch `app` cannot resolve it from the wrong tree, so
    it needs no anchor. Adding an `app` import to one of these moves it into
    `ANCHORED_SCRIPTS` automatically and the anchor tests start applying.
    """
    assert not _imports_app(ast.parse(script.read_text("utf-8")))


@pytest.mark.parametrize("script", ANCHORED_SCRIPTS, ids=lambda p: p.name)
def test_the_anchor_runs_before_the_first_app_import(script: Path) -> None:
    tree = ast.parse(script.read_text(encoding="utf-8"))
    anchor = anchor_index(tree)
    first_app = _first_top_level_app_import(tree)

    assert anchor >= 0, (
        f"{script.name} imports `app` but has no sys.path anchor. Copy the "
        "prologue from scripts/seed.py — see this module's docstring for why."
    )
    if first_app >= 0:
        assert anchor < first_app, (
            f"{script.name}'s anchor sits after its first `app` import, so it "
            "cannot affect how `app` resolves. Keep it in the import prologue."
        )


@pytest.mark.parametrize("script", ANCHORED_SCRIPTS, ids=lambda p: p.name)
def test_the_anchor_puts_this_checkout_on_sys_path(script: Path) -> None:
    out = _run_prologue(script)

    assert out["app"] == str(BACKEND_ROOT / "app" / "__init__.py"), out["app"]
    assert out["path"].count(str(BACKEND_ROOT)) == 1


@pytest.mark.parametrize("script", ANCHORED_SCRIPTS, ids=lambda p: p.name)
def test_the_scripts_directory_keeps_priority(script: Path) -> None:
    """`seed.py`'s bare `from seed_extras import ...` depends on this.

    The anchor adds the parent; it must never displace the entry CPython puts
    first for a script, or a sibling module in `scripts/` stops resolving.
    """
    out = _run_prologue(script)

    assert out["path"][0] == str(SCRIPTS_DIR)
    assert out["seed_extras"] == str(SCRIPTS_DIR / "seed_extras.py")


@pytest.mark.parametrize("script", ANCHORED_SCRIPTS, ids=lambda p: p.name)
def test_the_anchor_is_idempotent(script: Path) -> None:
    out = _run_prologue(script, times=3)

    assert out["path"].count(str(BACKEND_ROOT)) == 1


@pytest.mark.parametrize("script", ANCHORED_SCRIPTS, ids=lambda p: p.name)
def test_the_anchor_is_inert_when_the_checkout_already_resolves(script: Path) -> None:
    """The primary-checkout case, and the shim / PYTHONPATH case, are the same one.

    When `backend/` is already on `sys.path` — because the venv was installed
    from this checkout, because `sitecustomize.py` put it there, or because
    someone exported `PYTHONPATH` — the anchor must leave `sys.path` untouched
    rather than adding a second entry or reordering it.
    """
    out = _run_prologue(script, preseed=[str(SCRIPTS_DIR), str(BACKEND_ROOT)])

    assert out["path"] == out["before"]
    assert out["app"] == str(BACKEND_ROOT / "app" / "__init__.py")
