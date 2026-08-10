# Base image pinned by digest, same posture (and same base) as
# backend/Dockerfile — Scorecard's PinnedDependencies query treats an
# unpinned container image as a supply-chain pivot point, and a fixture
# whose Python version drifts under it stops being a deterministic
# fixture. Dependabot's docker ecosystem watches this directory and
# bumps the tag + digest together.
FROM python:3.14-slim@sha256:a7fb1e634c4a578f9e0bd6327f11a3cde11b7a9395f48e24360c0988bcc5c2bc

WORKDIR /srv

# `--require-hashes` is what makes this a pinned pip command: every
# package — transitive deps included — must match a hash in the lock, so
# a republished PyPI artifact can't slip in. Regenerate the lock from
# requirements.in with `uv pip compile … --generate-hashes`; the exact
# invocation is in requirements.in.
#
# The `requirements.in` + `requirements.txt` pair is deliberate, not a
# break from the backend's `.lock` naming: Dependabot only recognises a
# pip-compile lockfile when it ends in `.txt` and shares the `.in` file's
# basename, so this is what lets it open bump PRs here at all.
COPY requirements.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements.txt

COPY app.py .

EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
