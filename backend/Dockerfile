# Base image pinned by digest so a re-tagged python:3.14-slim can't
# silently change what we ship. Scorecard's PinnedDependencies query
# treats unpinned container images the same as unpinned actions — both
# leave a supply-chain pivot point. Bump the digest alongside the tag
# when Renovate / Dependabot opens a PR (the trailing comment is what
# the docker ecosystem reads as the human-version reference).
FROM python:3.14-slim@sha256:33ef7446e8c14b21cb247e23afbcdc90e98853b70812ca46b2265e769a7dfb8b

WORKDIR /app

# Install uv for fast dependency management. Pinned to the 0.5 release
# series by digest — `:latest` would re-resolve on every rebuild, which
# Scorecard flags and which can quietly swap the uv binary under us.
COPY --from=ghcr.io/astral-sh/uv:0.5@sha256:7bff3c3776ec467fc1437960f2c469d8beb30f536a6465a3350c647ccd260ec2 /uv /usr/local/bin/uv

COPY pyproject.toml .
RUN uv pip install --system -r pyproject.toml

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
