"""Alembic revision ids must fit the column that stores them.

``alembic_version.version_num`` is ``VARCHAR(32)``. A longer revision id is not
caught at authoring time, at import time, or by any linter — it surfaces as a
``StringDataRightTruncationError`` the first time ``alembic upgrade head`` runs,
which aborts the whole upgrade before applying anything.

That failure mode is worse than it sounds, because a deploy applies migrations
and then starts the app against the NEW model: the DB stays on the previous
revision while the code expects the new column, so every read of the affected
table 500s. Migration 0086 shipped a 33-character id and did exactly this to the
shared dev environment; its own docstring cited the trap and missed it by one
character.

These are cheap source-level checks, so they run without a database.
"""

import re
from pathlib import Path

VERSIONS_DIR = Path(__file__).resolve().parent.parent / "alembic" / "versions"

# The width of alembic_version.version_num. Alembic's own default.
MAX_REVISION_LENGTH = 32

_REVISION_RE = re.compile(r"^revision(?::\s*str)?\s*=\s*[\"']([^\"']+)[\"']", re.M)
_DOWN_REVISION_RE = re.compile(r"^down_revision(?::\s*[^=]+)?\s*=\s*[\"']([^\"']+)[\"']", re.M)


def _revisions() -> dict[str, Path]:
    found: dict[str, Path] = {}
    for path in sorted(VERSIONS_DIR.glob("*.py")):
        match = _REVISION_RE.search(path.read_text())
        if match:
            found[match.group(1)] = path
    return found


def test_every_revision_id_fits_the_version_column() -> None:
    too_long = {
        rev: (len(rev), path.name)
        for rev, path in _revisions().items()
        if len(rev) > MAX_REVISION_LENGTH
    }
    assert not too_long, (
        "alembic_version.version_num is VARCHAR"
        f"({MAX_REVISION_LENGTH}); these revision ids do not fit, so "
        f"`alembic upgrade head` aborts before applying anything: {too_long}"
    )


def test_revision_ids_are_unique() -> None:
    seen: dict[str, str] = {}
    duplicates: list[str] = []
    for path in sorted(VERSIONS_DIR.glob("*.py")):
        match = _REVISION_RE.search(path.read_text())
        if not match:
            continue
        rev = match.group(1)
        if rev in seen:
            duplicates.append(f"{rev}: {seen[rev]} and {path.name}")
        seen[rev] = path.name
    assert not duplicates, f"duplicate alembic revision ids: {duplicates}"


def test_every_down_revision_resolves() -> None:
    known = set(_revisions())
    dangling: list[str] = []
    for path in sorted(VERSIONS_DIR.glob("*.py")):
        match = _DOWN_REVISION_RE.search(path.read_text())
        if not match:
            continue
        parent = match.group(1)
        if parent not in known:
            dangling.append(f"{path.name} -> {parent}")
    assert not dangling, (
        "these migrations name a down_revision that no file defines, so the "
        f"chain is broken: {dangling}"
    )
