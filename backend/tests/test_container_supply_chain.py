"""Supply-chain guards on the repo's Dockerfiles.

These assert the two properties GitHub code scanning flagged and we fixed:
Scorecard's Pinned-Dependencies (base image by digest, pip installs by
hash) and the build-context hygiene that kept a stale local venv — and,
worse, a developer's `.env` — out of the shipped backend image.

Pure filesystem reads: no app import, no DB, no network. They're here
rather than in a shell lint because a failure should read as "you
un-pinned the image", not as a Scorecard score drifting on `main` days
after the change landed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

DOCKERFILES = [
    REPO_ROOT / "backend" / "Dockerfile",
    REPO_ROOT / "tools" / "fake-erp" / "Dockerfile",
]

# `FROM <image>@sha256:<64 hex>` — with or without a `:tag` in between, and
# with or without a trailing `AS <stage>`.
_PINNED_FROM = re.compile(
    r"^FROM\s+\S+@sha256:[0-9a-f]{64}(\s+AS\s+\S+)?\s*$",
    re.IGNORECASE,
)
# Same rule for the `COPY --from=<image>` form the backend uses to lift the
# uv binary out of a published image — it's a base image by another name.
_COPY_FROM_IMAGE = re.compile(r"^COPY\s+--from=(?P<ref>[^\s/][^\s]*)\s", re.IGNORECASE)


def _instructions(dockerfile: Path) -> list[str]:
    """Logical Dockerfile lines: comments dropped, continuations joined."""
    joined: list[str] = []
    buffer = ""
    for raw in dockerfile.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith("\\"):
            buffer += line[:-1].strip() + " "
            continue
        joined.append((buffer + line).strip())
        buffer = ""
    if buffer:
        joined.append(buffer.strip())
    return joined


@pytest.mark.parametrize("dockerfile", DOCKERFILES, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_base_images_are_pinned_by_digest(dockerfile: Path) -> None:
    """An unpinned `FROM` lets a re-tagged upstream change what we ship."""
    froms = [line for line in _instructions(dockerfile) if line.upper().startswith("FROM ")]
    assert froms, f"{dockerfile} has no FROM instruction"
    for line in froms:
        assert _PINNED_FROM.match(line), (
            f"{dockerfile.relative_to(REPO_ROOT)}: base image not pinned by digest: {line!r}. "
            "Use `FROM image:tag@sha256:<digest>` — Dependabot's docker ecosystem "
            "bumps tag and digest together."
        )


@pytest.mark.parametrize("dockerfile", DOCKERFILES, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_copied_in_images_are_pinned_by_digest(dockerfile: Path) -> None:
    """`COPY --from=<registry image>` pulls a foreign binary — pin it too.

    Named build stages (`COPY --from=builder`) are exempt: they resolve to
    a stage in this same file, not to a registry.
    """
    stages = {
        line.split()[-1].lower()
        for line in _instructions(dockerfile)
        if line.upper().startswith("FROM ") and re.search(r"\sAS\s", line, re.IGNORECASE)
    }
    for line in _instructions(dockerfile):
        match = _COPY_FROM_IMAGE.match(line)
        if not match:
            continue
        ref = match.group("ref")
        if ref.lower() in stages:
            continue
        assert re.search(r"@sha256:[0-9a-f]{64}$", ref), (
            f"{dockerfile.relative_to(REPO_ROOT)}: COPY --from image not pinned by digest: {ref!r}"
        )


@pytest.mark.parametrize("dockerfile", DOCKERFILES, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_pip_installs_require_hashes(dockerfile: Path) -> None:
    """Every install in an image resolves against a hash-pinned lock.

    Without `--require-hashes` a republished PyPI artifact silently enters
    the image, which is the whole point of the locks.
    """
    installs = [
        line for line in _instructions(dockerfile) if re.search(r"\b(uv\s+)?pip\s+install\b", line)
    ]
    assert installs, f"{dockerfile} installs nothing — did the install move?"
    for line in installs:
        assert "--require-hashes" in line, (
            f"{dockerfile.relative_to(REPO_ROOT)}: pip install without --require-hashes: {line!r}"
        )


def test_backend_dockerignore_keeps_secrets_and_venv_out_of_the_image() -> None:
    """`COPY . .` ships whatever the build context holds.

    A local `.venv` lands as a second, unmanaged Python install (and gets
    scanned as if installed); a gitignored `.env` holds real credentials
    and an image layer is not secret storage.
    """
    dockerignore = REPO_ROOT / "backend" / ".dockerignore"
    assert dockerignore.exists(), (
        "backend/.dockerignore is missing — backend/Dockerfile's `COPY . .` "
        "would bake the whole working tree, .env and .venv included, into a layer."
    )
    patterns = {
        line.strip()
        for line in dockerignore.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    for required in (".venv", ".env", ".env.*", "*.sops"):
        assert required in patterns, f"backend/.dockerignore must exclude {required!r}"


def test_backend_dockerignore_keeps_what_the_container_runs() -> None:
    """Guard the other direction: don't exclude a runtime dependency.

    `deploy/deploy.sh` runs `scripts/migrate_all_tenants.py` and
    `deploy/add-tenant.sh` runs `scripts/create_tenant.py` inside this
    image, so those paths must survive the filter.
    """
    dockerignore = REPO_ROOT / "backend" / ".dockerignore"
    patterns = {
        line.strip()
        for line in dockerignore.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    for kept in ("app", "alembic", "alembic.ini", "main.py", "scripts", "requirements.lock"):
        assert kept not in patterns, (
            f"backend/.dockerignore excludes {kept!r}, which the running container needs"
        )
