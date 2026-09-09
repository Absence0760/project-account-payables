"""`alembic.ini` must put THIS checkout's `backend/` on `sys.path`.

`alembic/env.py` does `from app.config import settings` / `from app.models
import Base`, and the `alembic` console script's `sys.path[0]` is the venv's
`bin/` — so nothing puts `app` on `sys.path` for it. In a git worktree reusing
the primary checkout's `backend/.venv`, the editable install's baked-in
`__editable___backend_0_1_0_finder` then answers `import app` with **the
primary checkout's** path. Measured, before `prepend_sys_path` was set:

    cd <worktree>/backend && alembic upgrade head --sql
    -> app.__file__ = <primary>/backend/app/__init__.py

For `upgrade` that runs the wrong tree's `env.py`; for `revision
--autogenerate` it diffs against the wrong tree's models and writes a
migration file that looks entirely plausible and is wrong.

Two properties are pinned here, and the second is the one that is easy to lose.
Alembic's own template suggests `prepend_sys_path = .`, and the value is
spliced onto `sys.path` **verbatim** (`sys.path[:0] = prepend_sys_path` in
`alembic/script/base.py`), so `.` is relative to the process CWD rather than to
the ini file. From the repo root it would add the repo root, where there is no
`app/`, and the fall-through is back. `%(here)s` — which alembic interpolates
to the ini's own absolute directory — is what makes it cwd-independent.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from alembic.config import Config

BACKEND_ROOT = Path(__file__).resolve().parent.parent
ALEMBIC_INI = BACKEND_ROOT / "alembic.ini"


def _config() -> Config:
    return Config(str(ALEMBIC_INI))


def test_prepend_sys_path_is_configured() -> None:
    value = _config().get_main_option("prepend_sys_path")
    assert value, (
        "alembic.ini sets no prepend_sys_path, so `alembic upgrade head` / "
        "`revision --autogenerate` resolve `app` through whatever else happens "
        "to be on sys.path — in a worktree, the primary checkout."
    )


def test_prepend_sys_path_is_this_checkout_regardless_of_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The `%(here)s`-vs-`.` property, measured from a foreign working directory."""
    monkeypatch.chdir(tmp_path)
    value = _config().get_main_option("prepend_sys_path")

    assert os.path.isabs(value), (
        f"prepend_sys_path resolved to {value!r}, which is relative. Alembic "
        "splices the value onto sys.path verbatim, so a relative value follows "
        "the process CWD instead of this ini — use %(here)s."
    )
    assert Path(value) == BACKEND_ROOT


def test_the_value_is_not_split_into_fragments() -> None:
    """`path_separator` is load-bearing, not cosmetic.

    Without it alembic warns and falls back to splitting `prepend_sys_path` on
    spaces, commas **and colons**. A checkout under a path containing a space
    would then be spliced onto `sys.path` as two directories that do not exist,
    `app` would not be found, and the editable-install fall-through this whole
    setting exists to close would quietly take over again.
    """
    prepend = _config().get_prepend_sys_paths_list()
    assert prepend == [str(BACKEND_ROOT)]


def test_the_prepended_directory_is_the_package_root() -> None:
    value = _config().get_main_option("prepend_sys_path")
    assert (Path(value) / "app" / "__init__.py").is_file()
    assert (Path(value) / "alembic" / "env.py").is_file()


def test_alembics_own_splice_resolves_app_from_this_checkout(tmp_path: Path) -> None:
    """Reproduce `sys.path[:0] = config.get_prepend_sys_paths_list()` and import `app`.

    Run under `-S -P`: `-S` skips `site`, so no `.pth` runs and the editable
    finder does not exist; `-P` keeps the cwd off `sys.path`. What is left is
    the stdlib plus whatever alembic prepends — so the import can only succeed
    via this setting, and it names the checkout it came from. Without `-S` the
    editable finder would answer and the check would pass either way.
    """
    prepend = _config().get_prepend_sys_paths_list()
    assert prepend, "alembic reports nothing to prepend"

    script = "import sys; sys.path[:0] = sys.argv[1:]; import app; print(app.__file__)"
    result = subprocess.run(
        [sys.executable, "-S", "-P", "-c", script, *prepend],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()) == BACKEND_ROOT / "app" / "__init__.py"
