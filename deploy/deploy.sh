#!/usr/bin/env bash
# Deploy / update the minimal single-VM stack (docs/minimal-deployment.md).
# Run ON the VM from anywhere: preflight → pull main → decrypt secrets →
# build frontend (in a node:20 container — no Node/pnpm needed on the VM) →
# build backend → run migrations BEFORE the new code serves traffic (control
# plane + every tenant DB — same ordering contract as the future ECS
# pipeline) → roll containers and wait for the API healthcheck.
#
# Usage: deploy.sh [--no-pull] [--backend-only|--frontend-only]
set -euo pipefail
cd "$(dirname "$0")"
REPO_ROOT=$(cd .. && pwd)

COMPOSE=(docker compose -f compose.prod.yml)
# Matches CI (ci.yml pins pnpm 9 on Node 20). The named volume caches the
# pnpm store across deploys so rebuilds don't re-download the world.
NODE_IMAGE=node:20-alpine
PNPM_SPEC=pnpm@9

die() {
	echo "deploy.sh: $*" >&2
	exit 1
}

DO_PULL=1 DO_BACKEND=1 DO_FRONTEND=1
for arg in "$@"; do
	case "$arg" in
	--no-pull) DO_PULL=0 ;;
	--backend-only) DO_FRONTEND=0 ;;
	--frontend-only) DO_BACKEND=0 ;;
	*) die "usage: deploy.sh [--no-pull] [--backend-only|--frontend-only]" ;;
	esac
done

# ── Preflight — fail with a clear message before doing any work ──────────────
for cmd in docker sops git; do
	command -v "$cmd" >/dev/null || die "'$cmd' not installed — run deploy/bootstrap-vm.sh first."
done
docker compose version >/dev/null 2>&1 || die "docker compose plugin missing — run deploy/bootstrap-vm.sh first."
docker info >/dev/null 2>&1 || die "cannot talk to the docker daemon — is it running, and are you in the docker group? (log out/in after bootstrap-vm.sh)"
[ -f .env.sops ] || die ".env.sops missing — author it from deploy/env.example, encrypt into the infra-secrets repo, and copy it here (see deploy/README.md)."

if [ "$DO_PULL" = 1 ]; then
	git -C "$REPO_ROOT" pull --ff-only
fi

# Secrets: decrypt fresh on every deploy (KMS access via the instance
# profile). Both files are gitignored.
umask 077
sops -d .env.sops >.env

# Everything compose interpolation / the app cannot default sensibly.
MISSING=""
for var in POSTGRES_PASSWORD APP_DOMAIN API_DOMAIN ACME_EMAIL AWS_REGION AP_SECRET_KEY; do
	grep -Eq "^${var}=.+" .env || MISSING="$MISSING $var"
done
[ -z "$MISSING" ] || die "required var(s) missing/empty in the sops env:$MISSING (contract: deploy/env.example)"

# Per-VM tenant host list for Caddy (gitignored) — seed from the example so
# the Caddyfile's `import tenants.caddy` always resolves.
[ -f tenants.caddy ] || cp tenants.caddy.example tenants.caddy

# ── Frontend ─────────────────────────────────────────────────────────────────
if [ "$DO_FRONTEND" = 1 ]; then
	# PUBLIC_API_URL is baked into the static build ($env/static/public).
	API_DOMAIN=$(grep -E '^API_DOMAIN=' .env | tail -1 | cut -d= -f2- || true)
	echo "==> building frontend (PUBLIC_API_URL=https://${API_DOMAIN})"
	docker run --rm \
		-v "$REPO_ROOT":/repo -w /repo/frontend \
		-v ap-prod-pnpm-store:/pnpm-store \
		-e npm_config_store_dir=/pnpm-store \
		-e PUBLIC_API_URL="https://${API_DOMAIN}" \
		"$NODE_IMAGE" sh -ec "npm i -g ${PNPM_SPEC} >/dev/null 2>&1 && pnpm install --frozen-lockfile && pnpm build"
fi

# ── Backend ──────────────────────────────────────────────────────────────────
if [ "$DO_BACKEND" = 1 ]; then
	echo "==> building backend image"
	"${COMPOSE[@]}" build api
	"${COMPOSE[@]}" up -d postgres redis
	echo "==> running migrations (control plane + every tenant DB)"
	"${COMPOSE[@]}" run --rm api sh -c \
		"alembic upgrade head && python scripts/migrate_all_tenants.py"
fi

# ── Roll + verify ────────────────────────────────────────────────────────────
echo "==> rolling containers"
"${COMPOSE[@]}" up -d --wait --wait-timeout 300

# Pick up Caddyfile / tenants.caddy edits without a container restart. A
# failure here is a real config error — do not suppress it.
"${COMPOSE[@]}" exec caddy caddy reload --config /etc/caddy/Caddyfile

echo "deploy complete — API healthcheck passed."
