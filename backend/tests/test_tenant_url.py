"""`app/tenant_url.py` — the one place a tenant DB name becomes a connection URL.

Two properties are asserted here, and the first is the load-bearing one.

**It must be importable with nothing else.** The three AWS Lambda handlers
(`extraction_lambda`, `erp_lambda`, `audit_lambda`) run outside the app process
and must not reach dotenv (backend/CLAUDE.md § "No dotenv in Lambda paths"), so
they cannot import `app.database` — importing it pulls in `app.config`, which
constructs the pydantic `Settings` and, in local dev, has already been fed by
`main.py`'s dotenv load. That constraint is exactly why each handler used to
carry its own copy of the URL construction. This module exists to end that, and
it only ends it while the import stays clean — so the check runs in a
**subprocess** that imports this module ALONE and inspects `sys.modules`. Doing
it in-process would prove nothing: pytest has already imported the whole app, so
`app.config` would be present no matter what this module does.

**And the construction must be safe by shape.** It cannot verify that `db_name`
came off a resolved `Organization` row — no static or runtime rule can, which is
the standing caveat in `tests/test_tenant_engine_construction.py`. It can verify
the weaker, checkable thing: a name that would re-shape the URL (a path
separator, a query start, an authority separator, a percent-escape) is refused
rather than concatenated.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from app.tenant_url import CONTROL_URL_ENV_VAR, control_url_from_env, make_tenant_url

BASE = "postgresql+asyncpg://u:p@host:5432/feohledger"


# --------------------------------------------------------------------------- #
# The import contract — checked in a subprocess, because in-process proves nothing
# --------------------------------------------------------------------------- #

_PROBE = """
import json, sys
import app.tenant_url  # the only thing this process imports from the app
loaded = sorted(m for m in sys.modules if m == "dotenv" or m.startswith(("app.", "dotenv.")))
print(json.dumps(loaded))
"""


def test_importing_the_module_pulls_in_no_app_or_dotenv_dependency():
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, f"probe failed:\n{proc.stderr}"
    loaded = json.loads(proc.stdout.strip().splitlines()[-1])

    # `app` itself is unavoidable (it is the parent package of `app.tenant_url`),
    # but nothing else under it — and nothing from dotenv — may be dragged in.
    assert loaded == ["app.tenant_url"], (
        "importing app.tenant_url now loads more than itself: "
        f"{loaded} — that breaks the reason it exists (the AWS Lambda handlers "
        "import it on a dotenv-free path where app.database/app.config are not "
        "importable). Keep it dependency-free."
    )


def test_config_is_genuinely_unimportable_free_lunch():
    """Sanity check on the probe: `app.database` DOES pull in `app.config`.

    Without this, the assertion above could pass because the probe is broken
    rather than because the module is clean.
    """
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            'import json, sys; import app.database; print(json.dumps("app.config" in sys.modules))',
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, f"probe failed:\n{proc.stderr}"
    assert json.loads(proc.stdout.strip().splitlines()[-1]) is True


# --------------------------------------------------------------------------- #
# make_tenant_url
# --------------------------------------------------------------------------- #


def test_only_the_database_name_is_replaced():
    """Driver, credentials, host and port survive; the trailing name is swapped."""
    assert make_tenant_url(BASE, "feoh_acme") == "postgresql+asyncpg://u:p@host:5432/feoh_acme"


def test_a_query_string_free_base_with_a_path_port_is_handled():
    assert make_tenant_url("postgresql+asyncpg://h/feohledger", "feoh_x").endswith("/feoh_x")


def test_it_is_the_body_app_database_binds():
    """`app.database._make_tenant_url` is this function plus `settings.database_url`.

    Pinned so the binding cannot quietly grow a second implementation — the exact
    drift `tests/test_tenant_engine_construction.py` now forbids structurally.
    """
    from app.config import settings
    from app.database import _make_tenant_url

    assert _make_tenant_url("feoh_acme") == make_tenant_url(settings.database_url, "feoh_acme")


@pytest.mark.parametrize(
    "hostile",
    [
        "feoh_acme/other",  # appends a path — connects to a different DB
        "feoh_acme?options=-csearch_path%3Devil",  # smuggles connection options
        "feoh_acme#frag",
        "other@evil-host:5432/feoh_victim",  # moves the authority section entirely
        "feoh%5Facme",  # percent-escape
        "feoh acme",
        "feoh_acme\n",
    ],
)
def test_a_name_that_would_reshape_the_url_is_refused(hostile):
    with pytest.raises(ValueError):
        make_tenant_url(BASE, hostile)


def test_an_empty_name_is_refused():
    with pytest.raises(ValueError):
        make_tenant_url(BASE, "")


def test_the_rejection_message_never_echoes_the_offending_value():
    """It can be attacker-supplied and it can reach a log (PII-out-of-logs)."""
    with pytest.raises(ValueError) as exc:
        make_tenant_url(BASE, "feoh_acme?leak=secret")
    assert "leak=secret" not in str(exc.value)


# --------------------------------------------------------------------------- #
# control_url_from_env
# --------------------------------------------------------------------------- #


def test_control_url_comes_from_the_named_variable():
    assert control_url_from_env({CONTROL_URL_ENV_VAR: BASE}) == BASE


def test_a_missing_control_url_raises_rather_than_defaulting():
    """A Lambda with no DATABASE_URL must fail the message (SQS redelivers, then
    dead-letters) — never proceed against some fallback."""
    with pytest.raises(KeyError):
        control_url_from_env({})
