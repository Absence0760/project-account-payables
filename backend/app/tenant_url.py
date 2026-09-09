"""Pure tenant-DB-URL derivation — no config, no dotenv, safe on the Lambda path.

``app/database.py::_make_tenant_url`` and the three AWS Lambda handlers
(``*_lambda.py``) both build a tenant connection URL by swapping the database
name in a base URL. The Lambda handlers must not import ``app.database`` — that
pulls in ``app.config``, and a Lambda entry point must not reach dotenv (see
``backend/CLAUDE.md`` § Conventions) — so they used to inline the one-line body,
held to mirroring it only by an AST guard. This module *is* that one line,
importable from both sides, so the two can no longer drift and the exemption
goes away.
"""

from __future__ import annotations


def tenant_db_url(base_url: str, db_name: str) -> str:
    """Return ``base_url`` with its trailing database-name segment replaced by
    ``db_name``.

    ``db_name`` must come from a resolved ``Organization`` row — never from a
    request — exactly as for every caller of ``app.database._make_tenant_url``
    (``tests/test_tenant_engine_construction.py`` pins that discipline).
    """
    return base_url.rsplit("/", 1)[0] + "/" + db_name
