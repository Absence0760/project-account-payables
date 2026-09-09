"""`scripts/worktree/sitecustomize.py` — the worktree editable-install shim.

Two properties, and the second matters as much as the first:

1. Run from a *different* checkout than the one `pip install -e` was run in,
   the shim puts that checkout on `sys.path` and drops the `__editable__*`
   finder, so `import app` can no longer fall through to the install-time tree.
2. Run from the install-time checkout it does **nothing at all**. A shim that
   fires there would break every ordinary `python main.py` / `pytest` run on
   the machine, which is a worse failure than the one it exists to fix.

Everything here is hermetic: two fake checkouts under `tmp_path` and a fake
finder shaped like the one setuptools writes. Nothing depends on this machine
actually having an editable install, on there being a git worktree, or on which
tree the suite happens to be running from — all three of which differ between a
laptop and CI, and any of which would turn a real regression into a skip.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from types import ModuleType

import pytest

BACKEND_ROOT = Path(__file__).resolve().parent.parent
SHIM_PATH = BACKEND_ROOT / "scripts" / "worktree" / "sitecustomize.py"
SHIM_DIR = SHIM_PATH.parent


def _load_shim() -> ModuleType:
    """Import the shim under a name that is NOT `sitecustomize`.

    The module installs itself only under `__name__ == "sitecustomize"`, so
    loading it this way exercises the functions without mutating this
    interpreter's `sys.meta_path` mid-test-session.
    """
    spec = importlib.util.spec_from_file_location("worktree_sitecustomize_under_test", SHIM_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


shim = _load_shim()


def _make_checkout(root: Path, package: str = "app") -> Path:
    """A minimal look-alike of this repo's `backend/` package root."""
    backend = root / "backend"
    (backend / package).mkdir(parents=True)
    (backend / package / "__init__.py").write_text(f"MARKER = {str(backend)!r}\n")
    (backend / "pyproject.toml").write_text('[project]\nname = "backend"\n')
    (backend / "scripts").mkdir()
    return backend


class _FakeEditableFinder:
    """Shaped like setuptools' generated `_EditableFinder`.

    `editable_mapping` reads `MAPPING` off the module named by `__module__`,
    which is how the real one is discovered — so the fake has to be registered
    in `sys.modules` under an `__editable__*` name, not merely named like one.
    """


def _register_fake_finder(monkeypatch: pytest.MonkeyPatch, mapping: dict[str, str]) -> type:
    module_name = "__editable___fakepkg_0_0_1_finder"
    module = ModuleType(module_name)
    module.MAPPING = dict(mapping)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, module_name, module)
    finder = type("_EditableFinder", (_FakeEditableFinder,), {})
    finder.__module__ = module_name
    return finder


# --------------------------------------------------------------------------
# resolve() — which checkout should serve the mapping?
# --------------------------------------------------------------------------


def test_inert_inside_the_checkout_the_install_points_at(tmp_path: Path) -> None:
    installed = _make_checkout(tmp_path / "primary")
    mapping = {"app": str(installed / "app")}

    for anchor in (installed, installed / "scripts", installed.parent):
        assert shim.resolve(mapping, [str(anchor)]) is None, anchor


def test_repoints_to_the_checkout_being_run_from(tmp_path: Path) -> None:
    installed = _make_checkout(tmp_path / "primary")
    worktree = _make_checkout(tmp_path / "worktree")
    mapping = {"app": str(installed / "app")}

    assert shim.resolve(mapping, [str(worktree)]) == str(worktree)


def test_repoints_from_a_subdirectory_of_the_worktree(tmp_path: Path) -> None:
    """`python scripts/seed.py` — `sys.path[0]` is `scripts/`, not `backend/`.

    This is the invocation that actually loses: `PathFinder` finds no `app` on
    `sys.path`, so the appended editable finder answers with the other tree.
    """
    installed = _make_checkout(tmp_path / "primary")
    worktree = _make_checkout(tmp_path / "worktree")
    mapping = {"app": str(installed / "app")}

    assert shim.resolve(mapping, [str(worktree / "scripts")]) == str(worktree)


def test_repoints_from_the_worktree_repo_root(tmp_path: Path) -> None:
    """One level above `backend/` — matched by re-rooting the mapped tail."""
    installed = _make_checkout(tmp_path / "primary")
    worktree = _make_checkout(tmp_path / "worktree")
    mapping = {"app": str(installed / "app")}

    assert shim.resolve(mapping, [str(tmp_path / "worktree")]) == str(worktree)


def test_no_checkout_in_sight_leaves_the_finder_alone(tmp_path: Path) -> None:
    """Outside any checkout there is nothing to prove, so nothing is changed.

    Serving the install-time tree is the *documented* behaviour of an editable
    install; overriding it on a guess would be the shim inventing an answer.
    """
    installed = _make_checkout(tmp_path / "primary")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    mapping = {"app": str(installed / "app")}

    assert shim.resolve(mapping, [str(elsewhere)]) is None


def test_a_lookalike_tree_without_a_pyproject_is_not_accepted(tmp_path: Path) -> None:
    """The candidate has to be laid out like the install-time root."""
    installed = _make_checkout(tmp_path / "primary")
    impostor = tmp_path / "impostor" / "backend"
    (impostor / "app").mkdir(parents=True)
    (impostor / "app" / "__init__.py").write_text("")
    mapping = {"app": str(installed / "app")}

    assert shim.resolve(mapping, [str(impostor)]) is None


def test_a_mapping_spanning_several_roots_is_declined(tmp_path: Path) -> None:
    installed = _make_checkout(tmp_path / "primary")
    other = tmp_path / "primary" / "tools"
    other.mkdir()
    mapping = {"app": str(installed / "app"), "tools": str(other)}

    assert shim.install_root(mapping) is None
    assert shim.resolve(mapping, [str(installed)]) is None


def test_working_directory_outranks_the_script_directory() -> None:
    """A console script's `argv[0]` lives in the venv — inside the *primary*
    checkout — so it must never be able to overrule where the developer stood."""
    anchors = shim.default_anchors(argv=["/usr/bin/python3"], cwd="/tmp")
    assert anchors[0] == os.path.realpath("/tmp")


# --------------------------------------------------------------------------
# install() — what it does to sys.meta_path / sys.path
# --------------------------------------------------------------------------


def test_install_drops_the_finder_and_adds_the_local_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installed = _make_checkout(tmp_path / "primary")
    worktree = _make_checkout(tmp_path / "worktree")
    finder = _register_fake_finder(monkeypatch, {"app": str(installed / "app")})

    meta_path = ["something-else", finder]
    path: list[str] = []

    changed = shim.install(meta_path=meta_path, path=path, anchors=[str(worktree / "scripts")])

    assert changed == [(finder, str(worktree))]
    assert meta_path == ["something-else"]
    assert path == [str(worktree)]


def test_install_is_a_no_op_in_the_installing_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installed = _make_checkout(tmp_path / "primary")
    finder = _register_fake_finder(monkeypatch, {"app": str(installed / "app")})

    meta_path = ["something-else", finder]
    path = ["untouched"]

    assert shim.install(meta_path=meta_path, path=path, anchors=[str(installed / "scripts")]) == []
    assert meta_path == ["something-else", finder]
    assert path == ["untouched"]


def test_install_ignores_finders_that_are_not_editable_installs(tmp_path: Path) -> None:
    worktree = _make_checkout(tmp_path / "worktree")

    class _Ordinary:
        pass

    meta_path = [_Ordinary]
    path: list[str] = []

    assert shim.install(meta_path=meta_path, path=path, anchors=[str(worktree)]) == []
    assert meta_path == [_Ordinary]
    assert path == []


def test_a_malformed_editable_module_is_ignored_rather_than_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Interpreter startup must survive a mapping shaped unlike we expect."""
    worktree = _make_checkout(tmp_path / "worktree")
    module_name = "__editable___broken_finder"
    module = ModuleType(module_name)
    module.MAPPING = "not-a-dict"  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, module_name, module)
    finder = type("_EditableFinder", (), {})
    finder.__module__ = module_name

    meta_path = [finder]
    assert shim.install(meta_path=meta_path, path=[], anchors=[str(worktree)]) == []
    assert meta_path == [finder]


# --------------------------------------------------------------------------
# End to end, in a real interpreter
# --------------------------------------------------------------------------


_DRIVER = textwrap.dedent(
    """
    import sys

    # Stand in for the .pth setuptools drops in site-packages: append the
    # finder AFTER PathFinder, exactly as `install()` in the generated
    # __editable___*_finder module does.
    sys.path.insert(0, {fake_site!r})
    import __editable___faketree_0_0_1_finder as fake_finder
    fake_finder.install()

    sys.argv = ["scripts/seed.py"]
    import sitecustomize
    sitecustomize.install()

    import app
    print("SHIM:", sitecustomize.__file__)
    print("APP:", app.MARKER)
    print(
        "FINDER:",
        any(getattr(f, "__module__", "").startswith("__editable__") for f in sys.meta_path),
    )
    """
)


def _write_fake_site(fake_site: Path, mapped: Path) -> None:
    fake_site.mkdir(parents=True, exist_ok=True)
    (fake_site / "__editable___faketree_0_0_1_finder.py").write_text(
        textwrap.dedent(
            f"""
            import sys
            from importlib.util import spec_from_file_location

            MAPPING = {{"app": {str(mapped)!r}}}


            class _EditableFinder:
                @classmethod
                def find_spec(cls, fullname, path=None, target=None):
                    if fullname in MAPPING:
                        return spec_from_file_location(
                            fullname, MAPPING[fullname] + "/__init__.py"
                        )
                    return None


            def install():
                sys.meta_path.append(_EditableFinder)
            """
        )
    )


def _run_driver(cwd: Path, fake_site: Path) -> dict[str, str]:
    env = dict(os.environ)
    # The shim is activated exactly as the README tells a developer to.
    env["PYTHONPATH"] = str(SHIM_DIR)
    env.pop("PYTHONSAFEPATH", None)
    result = subprocess.run(
        [sys.executable, "-c", _DRIVER.format(fake_site=str(fake_site))],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    wanted = ("SHIM", "APP", "FINDER")
    lines = [ln for ln in result.stdout.splitlines() if ln.startswith(wanted)]
    return dict(line.split(": ", 1) for line in lines)


def test_end_to_end_a_worktree_imports_its_own_tree(tmp_path: Path) -> None:
    installed = _make_checkout(tmp_path / "primary")
    worktree = _make_checkout(tmp_path / "worktree")
    fake_site = tmp_path / "fakesite"
    _write_fake_site(fake_site, installed / "app")

    # `scripts/` is the cwd, mirroring `python scripts/seed.py`: nothing puts
    # the worktree's `backend/` on sys.path, so without the shim the finder
    # answers with the primary checkout.
    out = _run_driver(worktree / "scripts", fake_site)

    assert out["SHIM"] == str(SHIM_PATH)
    assert out["APP"] == str(worktree)
    assert out["FINDER"] == "False"


def test_end_to_end_the_installing_checkout_is_untouched(tmp_path: Path) -> None:
    installed = _make_checkout(tmp_path / "primary")
    fake_site = tmp_path / "fakesite"
    _write_fake_site(fake_site, installed / "app")

    out = _run_driver(installed / "scripts", fake_site)

    assert out["SHIM"] == str(SHIM_PATH)
    assert out["APP"] == str(installed)
    # Still there, still doing its job — the shim declined to intervene.
    assert out["FINDER"] == "True"


def test_pythonpath_is_what_activates_the_shim(tmp_path: Path) -> None:
    """The README's activation instruction, asserted rather than assumed.

    `sitecustomize` is auto-imported by `site` only if it is importable at
    interpreter *startup* — before `sys.path[0]` (the script's directory) is
    inserted. `PYTHONPATH` is present by then; a plain `cd` into the directory
    is not, which is the part that is easy to get wrong.
    """
    script = tmp_path / "probe.py"
    script.write_text("import sitecustomize; print(sitecustomize.__file__)")

    env = dict(os.environ)
    env["PYTHONPATH"] = str(SHIM_DIR)
    with_pythonpath = subprocess.run(
        [sys.executable, str(script)], env=env, capture_output=True, text=True, timeout=60
    )
    assert with_pythonpath.returncode == 0, with_pythonpath.stderr
    assert with_pythonpath.stdout.strip() == str(SHIM_PATH)

    env_cwd_only = dict(os.environ)
    env_cwd_only.pop("PYTHONPATH", None)
    without = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(SHIM_DIR),
        env=env_cwd_only,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert without.returncode != 0
    assert "sitecustomize" in without.stderr
