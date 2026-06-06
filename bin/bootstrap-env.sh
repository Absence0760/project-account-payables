#!/usr/bin/env bash
#
# bootstrap-env.sh — stamp local-dev .env files from their committed
# .env.example templates.
#
# Local-first: every value in the .env.example files is a safe, no-risk local
# default (loopback URLs, mock adapters, `change-me` JWT key, MinIO's
# minioadmin/minioadmin). This script copies each .env.example to .env when the
# .env is missing so a fresh clone runs with zero manual secret setup. Deployed
# secrets never live here — they're in the *.sops files (see backend/CLAUDE.md
# § Secrets management) and bin/sops-init.sh.
#
# Idempotent and non-destructive: an existing .env is left untouched, so any
# local overrides you've made survive. Safe to run on every `pnpm dev`.
set -euo pipefail

# Repo root = parent of this script's directory, regardless of CWD.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Apps whose .env is seeded from a committed .env.example template.
# (mobile/ needs none — lib/config.dart hardcodes the localhost default; the
#  backend runs off app/config.py defaults even without a .env, but we still
#  stamp one so `pnpm dev:backend` has something to source and overrides have a
#  home.)
APPS=(backend frontend)

created=0
for app in "${APPS[@]}"; do
    example="$ROOT/$app/.env.example"
    target="$ROOT/$app/.env"

    if [ ! -f "$example" ]; then
        echo "  ⚠  $app/.env.example missing — skipping (nothing to copy from)" >&2
        continue
    fi

    if [ -f "$target" ]; then
        # Already present — respect any local overrides, say nothing noisy.
        continue
    fi

    cp "$example" "$target"
    echo "  → created $app/.env from $app/.env.example (local defaults)"
    created=$((created + 1))
done

if [ "$created" -eq 0 ]; then
    echo "  ✓ env files already present — local defaults in place"
fi
