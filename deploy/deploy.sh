#!/usr/bin/env bash
# Deploy / update the minimal single-VM stack (docs/minimal-deployment.md).
# Run ON the VM from anywhere: pulls main, decrypts secrets, rebuilds the
# frontend + backend, runs migrations BEFORE the new code serves traffic
# (control plane + every tenant DB — same ordering contract as the future
# ECS pipeline), then rolls the containers.
#
# Usage: deploy.sh [--no-pull] [--backend-only|--frontend-only]
set -euo pipefail
cd "$(dirname "$0")"

COMPOSE=(docker compose -f compose.prod.yml)
DO_PULL=1 DO_BACKEND=1 DO_FRONTEND=1
for arg in "$@"; do
	case "$arg" in
	--no-pull) DO_PULL=0 ;;
	--backend-only) DO_FRONTEND=0 ;;
	--frontend-only) DO_BACKEND=0 ;;
	*)
		echo "usage: deploy.sh [--no-pull] [--backend-only|--frontend-only]" >&2
		exit 2
		;;
	esac
done

if [ "$DO_PULL" = 1 ]; then
	git -C .. pull --ff-only
fi

# Secrets: the sops-encrypted env lives in the private infra-secrets repo and
# is copied onto this VM as deploy/.env.sops. Decrypt fresh on every deploy
# (KMS access comes from the instance profile). Both files are gitignored.
if [ ! -f .env.sops ]; then
	echo "deploy/.env.sops missing — copy it from the infra-secrets repo first." >&2
	exit 1
fi
umask 077
sops -d .env.sops >.env

# Per-VM tenant host list for Caddy (gitignored) — seed from the example on
# first run so the Caddyfile's `import tenants.caddy` always resolves.
[ -f tenants.caddy ] || cp tenants.caddy.example tenants.caddy

if [ "$DO_FRONTEND" = 1 ]; then
	# PUBLIC_API_URL is baked into the static build ($env/static/public).
	API_DOMAIN=$(grep -E '^API_DOMAIN=' .env | tail -1 | cut -d= -f2- || true)
	if [ -z "$API_DOMAIN" ]; then
		echo "API_DOMAIN not set in the sops env." >&2
		exit 1
	fi
	pnpm -C ../frontend install --frozen-lockfile
	PUBLIC_API_URL="https://${API_DOMAIN}" pnpm -C ../frontend build
fi

if [ "$DO_BACKEND" = 1 ]; then
	"${COMPOSE[@]}" build api
	"${COMPOSE[@]}" up -d postgres redis
	"${COMPOSE[@]}" run --rm api sh -c \
		"alembic upgrade head && python scripts/migrate_all_tenants.py"
fi

"${COMPOSE[@]}" up -d

# Pick up Caddyfile / tenants.caddy edits without a container restart. A
# failure here is a real config error — do not suppress it.
"${COMPOSE[@]}" exec caddy caddy reload --config /etc/caddy/Caddyfile

echo "deploy complete."
