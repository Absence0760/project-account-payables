#!/usr/bin/env bash
# Provision a tenant end-to-end on the minimal single-VM stack: tenant DB +
# org + admin user (via the same provision_tenant path as self-service
# signup), the Caddy host block, and a zero-downtime reload. With the
# recommended wildcard DNS record (*.APP_DOMAIN → this VM) no DNS work is
# needed at all.
#
# Usage: add-tenant.sh <slug> --name "Acme Corp" --admin-email a@acme.com
#                      [--admin-password <pw>]
# Without --admin-password a temp password is generated and first-login
# change is forced.
set -euo pipefail
cd "$(dirname "$0")"

COMPOSE=(docker compose -f compose.prod.yml)

die() {
	echo "add-tenant.sh: $*" >&2
	exit 1
}
usage() {
	die "usage: add-tenant.sh <slug> --name <company> --admin-email <email> [--admin-password <pw>]"
}

SLUG="${1:-}"
[ -n "$SLUG" ] || usage
shift
NAME="" EMAIL="" PASSWORD=""
while [ $# -gt 0 ]; do
	case "$1" in
	--name) NAME="${2:-}" && shift 2 ;;
	--admin-email) EMAIL="${2:-}" && shift 2 ;;
	--admin-password) PASSWORD="${2:-}" && shift 2 ;;
	*) usage ;;
	esac
done
[ -n "$NAME" ] && [ -n "$EMAIL" ] || usage
echo "$SLUG" | grep -Eq '^[a-z0-9](-?[a-z0-9])*$' || die "invalid slug '$SLUG' (lowercase letters, digits, single hyphens)"
[ -f .env ] || die "deploy/.env missing — run deploy.sh at least once first."

APP_DOMAIN=$(grep -E '^APP_DOMAIN=' .env | tail -1 | cut -d= -f2- || true)
[ -n "$APP_DOMAIN" ] || die "APP_DOMAIN not set in the sops env."
HOST="${SLUG}.${APP_DOMAIN}"

FORCE_CHANGE=()
if [ -z "$PASSWORD" ]; then
	# Prefix guarantees the app's complexity rules (12+ chars, upper/lower/digit).
	PASSWORD="Aa1-$(openssl rand -hex 12)"
	FORCE_CHANGE=(--force-password-change)
fi

"${COMPOSE[@]}" exec -T api python scripts/create_tenant.py \
	--name "$NAME" --slug "$SLUG" \
	--admin-email "$EMAIL" --admin-password "$PASSWORD" \
	"${FORCE_CHANGE[@]}"

if ! grep -q "^${HOST} {" tenants.caddy 2>/dev/null; then
	printf '\n%s {\n\timport spa\n}\n' "$HOST" >>tenants.caddy
	"${COMPOSE[@]}" exec caddy caddy reload --config /etc/caddy/Caddyfile
fi

echo
echo "tenant ready: https://${HOST}"
echo "  admin login: ${EMAIL}"
if [ ${#FORCE_CHANGE[@]} -gt 0 ]; then
	echo "  temp password: ${PASSWORD}  (share securely; change forced at first login)"
fi
echo "  DNS: covered by a wildcard *.${APP_DOMAIN} record; otherwise add an A record for ${HOST}."
