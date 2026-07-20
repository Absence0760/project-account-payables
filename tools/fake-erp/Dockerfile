# Base image pinned by digest, same posture (and same base) as
# backend/Dockerfile — Scorecard's PinnedDependencies query treats an
# unpinned container image as a supply-chain pivot point, and a fixture
# whose Python version drifts under it stops being a deterministic
# fixture. Dependabot's docker ecosystem watches this directory and
# bumps the tag + digest together.
FROM python:3.14-slim@sha256:cea0e6040540fb2b965b6e7fb5ffa00871e632eef63719f0ea54bca189ce14a6

WORKDIR /srv

# `--require-hashes` is what makes this a pinned pip command: every
# package — transitive deps included — must match a hash in the lock, so
# a republished PyPI artifact can't slip in. Regenerate the lock from
# requirements.in with `uv pip compile … --generate-hashes`; the exact
# invocation is in requirements.in.
COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

COPY app.py .

EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
