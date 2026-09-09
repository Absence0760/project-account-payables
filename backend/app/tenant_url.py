"""Database-URL primitives for code paths that cannot import ``app.config``.

Why this module exists at all: three AWS Lambda handlers
(``extraction_lambda``, ``erp_lambda``, ``audit_lambda``) run outside the app
process and must not reach dotenv (backend/CLAUDE.md § "No dotenv in Lambda
paths"), so they cannot import ``app.database`` — importing it pulls in
``app.config``. For a long time each therefore inlined ``_make_tenant_url``'s
one-line body, and a guard (``tests/test_tenant_engine_construction.py``) held
the three copies to mirroring the original. Three copies plus a structural
guard is a worse answer than one function: this module IS that function, and
the guard now forbids the duplication instead of policing it.

**This module must stay import-free.** Not "light on imports" — free of them.
``os`` is the single exception, and only inside :func:`control_url_from_env`.
Anything reaching ``app.config`` (directly or transitively) re-creates the
dotenv problem and puts the handlers back on their own copies.
``tests/test_tenant_url.py`` asserts that in a subprocess, by importing this
module alone and checking what landed in ``sys.modules``.
"""

from __future__ import annotations

import os

#: The environment variable a Lambda handler reads the control-plane URL from.
#: Named once here rather than spelled out at each handler, so "which variable"
#: is a single fact.
CONTROL_URL_ENV_VAR = "DATABASE_URL"

#: Characters that would let a database NAME re-shape the connection URL it is
#: spliced into — a path separator, a query/fragment start, an authority
#: separator, a percent-escape, a backslash. A tenant DB name is ``feoh_<slug>``
#: off a resolved ``Organization`` row and contains none of them; a value that
#: does is not a database name, whatever it claims to be.
_URL_STRUCTURAL_CHARS = frozenset("/?#@:\\%\"' \t\r\n")


def control_url_from_env(env: dict[str, str] | None = None) -> str:
    """The control-plane database URL, read from the process environment.

    ``env`` exists for tests; production passes nothing and reads
    ``os.environ``. Raises ``KeyError`` when the variable is unset — a Lambda
    with no database URL must fail its message (so SQS redelivers, then
    dead-letters) rather than proceed against some default.
    """
    source = os.environ if env is None else env
    return source[CONTROL_URL_ENV_VAR]


def make_tenant_url(base_url: str, db_name: str) -> str:
    """Swap the database name in ``base_url`` for ``db_name``.

    ``base_url`` is the control-plane URL — everything that identifies the
    server (driver, credentials, host, port, query string) is kept; only the
    trailing database name is replaced. ``db_name`` must come from a resolved
    ``Organization`` row: this function cannot check that (no static or runtime
    rule can), which is exactly why every caller routes through one place a
    reviewer can look at.

    It CAN check the weaker, checkable thing — that the name cannot re-shape
    the URL. A name carrying ``/``, ``?``, ``#``, ``@``, ``:``, ``%`` or
    whitespace would append a path, start a query, or move the authority
    section, so it is refused rather than concatenated.
    """
    if not db_name:
        raise ValueError("tenant database name is empty")
    if any(ch in _URL_STRUCTURAL_CHARS for ch in db_name):
        # The offending value is not echoed: it may be attacker-supplied, and
        # this string can reach a log.
        raise ValueError("tenant database name contains URL-structural characters")
    return base_url.rsplit("/", 1)[0] + "/" + db_name
