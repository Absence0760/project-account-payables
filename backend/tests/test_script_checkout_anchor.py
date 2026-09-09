"""`scripts/seed.py` and `scripts/migrate_all_tenants.py` anchor their own checkout.

Run as `python scripts/seed.py`, `sys.path[0]` is `scripts/` — so `app` is not
on `sys.path` at all. A git worktree reusing the primary checkout's
`backend/.venv` then resolves it through the editable install's baked-in
`__editable___backend_0_1_0_finder`, which maps `app` to whichever checkout
`pip install -e` was run in. Measured, before the anchor:

    cd <worktree>/backend && python scripts/seed.py --help
    -> app.__file__ = <primary>/backend/app/__init__.py

These are the first two commands a contributor runs (`pnpm seed`,
`pnpm migrate:all`; step 4 of the root CLAUDE.md first-time setup), and both
were silently operating on another checkout's models while every line they
printed named this one. `scripts/worktree/sitecustomize.py` fixes the whole
class, but only once someone puts it on `PYTHONPATH`; these two are ours to
edit, so they carry the two-line anchor and need no activation.

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
ANCHORED_SCRIPTS = [SCRIPTS_DIR / "seed.py", SCRIPTS_DIR / "migrate_all_tenants.py"]

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


def _first_app_import_index(tree: ast.Module) -> int:
    for i, node in enumerate(tree.body):
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "app":
            return i
        if isinstance(node, ast.Import) and any(a.name.split(".")[0] == "app" for a in node.names):
            return i
    return -1


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


@pytest.mark.parametrize("script", ANCHORED_SCRIPTS, ids=lambda p: p.name)
def test_the_anchor_runs_before_the_first_app_import(script: Path) -> None:
    tree = ast.parse(script.read_text(encoding="utf-8"))
    anchor = anchor_index(tree)
    first_app = _first_app_import_index(tree)

    assert anchor >= 0, f"{script.name} has no sys.path anchor — see this module's docstring"
    assert first_app >= 0, f"{script.name} imports nothing from app; is this still the right file?"
    assert anchor < first_app, (
        f"{script.name}'s anchor sits after its first `app` import, so it cannot "
        "affect how `app` resolves. Keep it in the import prologue."
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
