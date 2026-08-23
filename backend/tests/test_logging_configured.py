"""Regression: `app.main` used to never call `logging.basicConfig`, so the
root logger had no handler and defaulted to WARNING. Every INFO-level log
anywhere under `app/` — most visibly the console email adapter's "email sent"
dump, which swallowed the signup-verification link, welcome email, and
vendor-portal invite in local dev — was silently dropped. This was NOT a
local-dev-only issue: uvicorn's default log config only wires up its OWN
"uvicorn"/"uvicorn.access" loggers, never root, so the same silence applied
to every deployed process too.

Run in a fresh subprocess rather than asserting on the current process's root
logger: pytest's own logging-capture plugin pre-attaches a handler to root,
which would make an assertion here pass regardless of whether `app.main`
itself does its job — exactly the kind of false-positive this regression
needs to not have.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent


#: A distinctive prefix for the two lines this check cares about, so a stray
#: line some third-party import prints to stdout (observed in CI, not
#: reproducible locally — almost certainly a one-time notice on a cold venv)
#: can't shift a positional split and fail the whole check on unrelated noise.
_MARKER = "FEOH_LOGGING_CHECK:"


def _run_import_check(*, debug_env: str) -> tuple[int, int]:
    script = (
        "import logging\n"
        "import app.main\n"
        "root = logging.getLogger()\n"
        f"print({_MARKER!r} + 'handlers=' + str(len(root.handlers)))\n"
        f"print({_MARKER!r} + 'level=' + str(root.getEffectiveLevel()))\n"
    )
    env = {**os.environ, "FEOH_DEBUG": debug_env}
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(BACKEND_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    marked = [
        line[len(_MARKER) :] for line in result.stdout.splitlines() if line.startswith(_MARKER)
    ]
    values = {k: v for k, v in (line.split("=", 1) for line in marked)}
    assert values.keys() == {"handlers", "level"}, (result.stdout, result.stderr)
    return int(values["handlers"]), int(values["level"])


def test_app_main_configures_root_logger_handler():
    """Merely importing app.main must give the root logger a handler — the
    thing that was missing entirely before this fix."""
    handler_count, _ = _run_import_check(debug_env="true")
    assert handler_count >= 1


def test_app_main_uses_debug_level_when_settings_debug_true():
    _, level = _run_import_check(debug_env="true")
    assert level == logging.DEBUG


def test_app_main_uses_info_level_when_settings_debug_false():
    """Deployed default (FEOH_DEBUG unset/false): INFO, not the stdlib
    default of WARNING — INFO-level operational logs (sweep progress,
    adapter activity) must reach a handler in production too."""
    _, level = _run_import_check(debug_env="false")
    assert level == logging.INFO
