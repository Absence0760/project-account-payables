# Base image pinned by digest so a re-tagged python:3.14-slim can't
# silently change what we ship. Scorecard's PinnedDependencies query
# treats unpinned container images the same as unpinned actions — both
# leave a supply-chain pivot point. Bump the digest alongside the tag
# when Renovate / Dependabot opens a PR (the trailing comment is what
# the docker ecosystem reads as the human-version reference).
FROM python:3.14-slim@sha256:7a500125bc50693f2214e842a621440a1b1b9cbb2188f74ab045d29ed2ea5856

# Pull Debian security backports on top of the pinned base layer. The
# digest above only changes when the upstream python:3.14-slim manifest
# rebuilds, which lags Debian's trixie-security stream by days-to-weeks.
# Trivy + GitHub code scanning flag CVEs in libc6 / libcap2 / libsystemd0
# / libudev1 / sed every time that gap opens; this step closes it without
# waiting for an upstream rebuild. `--no-install-recommends` keeps the
# image lean; the apt list cleanup keeps the layer small.
RUN apt-get update \
    && apt-get -y --no-install-recommends upgrade \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install uv for fast dependency management. Pinned to the 0.5 release
# series by digest — `:latest` would re-resolve on every rebuild, which
# Scorecard flags and which can quietly swap the uv binary under us.
COPY --from=ghcr.io/astral-sh/uv:0.5@sha256:7bff3c3776ec467fc1437960f2c469d8beb30f536a6465a3350c647ccd260ec2 /uv /usr/local/bin/uv

# Runtime deps install from a fully hash-pinned lock so a compromised or
# republished PyPI artifact can't slip into the image — same Scorecard
# PinnedDependencies posture as CI. `requirements.lock` is the runtime-only
# (no [dev]/[ocr] extras) sibling of requirements-dev.lock; regenerate both
# from pyproject.toml with `uv pip compile … --generate-hashes` — see
# backend/CLAUDE.md → Dependency lock. The app itself runs from the copied
# source (no editable install), so only its dependencies are locked here.
COPY pyproject.toml requirements.lock .
RUN uv pip install --system --require-hashes -r requirements.lock

COPY . .

EXPOSE 8000

# Default entrypoint is the ECS/Fargate API (uvicorn). This same image is
# ALSO deployed as the async-worker Lambdas (extraction / ERP / audit): on
# Lambda the function's ImageConfig overrides the entrypoint to
# `python -m awslambdaric` + its handler, so no Lambda-specific image is
# built. The awslambdaric runtime client is bundled via requirements.lock —
# see pyproject.toml + docs/production-deployment.md → Lambda workers.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
