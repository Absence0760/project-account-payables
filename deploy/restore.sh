#!/usr/bin/env bash
# Restore Postgres from the nightly S3 backups written by deploy/backup.sh
# (docs/minimal-deployment.md § Backups). This is the other half of the DR
# story — run it against a scratch stack once BEFORE you ever need it.
#
# Streams everything straight from S3 (nothing persists on local disk):
#   1. role globals via psql — "already exists" errors are benign on a
#      non-fresh cluster (pg_dumpall globals aren't idempotent)
#   2. each requested DB via pg_restore --create; a DB that already exists
#      is skipped unless --force, which drops + recreates it
#
# The api container is stopped for the duration (its open connections would
# block DROP/CREATE DATABASE) and the stack is rolled back up at the end.
#
# Usage: restore.sh <YYYY-MM-DD> [--force] [db ...]
#   db ...    restore only these databases (default: every .dump under the
#             date prefix)
#   --force   drop + recreate databases that already exist
set -euo pipefail
cd "$(dirname "$0")"

COMPOSE=(docker compose -f compose.prod.yml)

die() {
	echo "restore.sh: $*" >&2
	exit 1
}

STAMP="${1:-}"
echo "$STAMP" | grep -Eq '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' ||
	die "usage: restore.sh <YYYY-MM-DD> [--force] [db ...]"
shift

FORCE=0
DBS=()
for arg in "$@"; do
	case "$arg" in
	--force) FORCE=1 ;;
	*) DBS+=("$arg") ;;
	esac
done

BUCKET="${BACKUP_S3_BUCKET:-}"
if [ -z "$BUCKET" ] && [ -f .env ]; then
	BUCKET=$(grep -E '^BACKUP_S3_BUCKET=' .env | tail -1 | cut -d= -f2- || true)
fi
[ -n "$BUCKET" ] || die "BACKUP_S3_BUCKET not set (env var or deploy/.env)."

# Off-EC2 (e.g. Hetzner) there is no IMDS to infer a region from — reuse the
# AWS_REGION the sops env carries (same fallback as backup.sh).
if [ -z "${AWS_DEFAULT_REGION:-}" ] && [ -z "${AWS_REGION:-}" ] && [ -f .env ]; then
	REGION=$(grep -E '^AWS_REGION=' .env | tail -1 | cut -d= -f2- || true)
	if [ -n "$REGION" ]; then
		export AWS_DEFAULT_REGION="$REGION"
	fi
fi

PREFIX="s3://${BUCKET}/pg/${STAMP}"

if [ ${#DBS[@]} -eq 0 ]; then
	mapfile -t DBS < <(aws s3 ls "${PREFIX}/" | awk '{print $NF}' | grep '\.dump$' | sed 's/\.dump$//')
fi
[ ${#DBS[@]} -gt 0 ] || die "no dumps found under ${PREFIX}/ (wrong date? wrong bucket?)"

# Defense against odd keys in the bucket ending up interpolated into SQL /
# shell below — backup.sh only ever writes feohledger / feoh_* dumps. Same
# shape the backend's _SAFE_DB_NAME allows: tenant slugs contain hyphens
# (feoh_acme-corp), so the hyphen must be admitted here too.
for db in "${DBS[@]}"; do
	echo "$db" | grep -Eq '^[a-z][a-z0-9_-]*$' || die "unexpected database name '$db' in the backup listing"
done

"${COMPOSE[@]}" up -d postgres
echo "==> stopping api (open connections block DROP/CREATE DATABASE)"
"${COMPOSE[@]}" stop api

echo "==> restoring role globals ('already exists' errors are benign on a non-fresh cluster)"
aws s3 cp "${PREFIX}/globals.sql.gz" - | gunzip |
	"${COMPOSE[@]}" exec -T postgres psql -U postgres

for db in "${DBS[@]}"; do
	EXISTS=$("${COMPOSE[@]}" exec -T postgres psql -U postgres -Atc \
		"SELECT 1 FROM pg_database WHERE datname = '${db}'")
	CLEAN=()
	if [ "$EXISTS" = "1" ]; then
		if [ "$FORCE" != 1 ]; then
			echo "==> ${db}: already exists — skipped (re-run with --force to drop + recreate)"
			continue
		fi
		CLEAN=(--clean --if-exists)
		echo "==> ${db}: dropping + restoring"
	else
		echo "==> ${db}: restoring"
	fi
	aws s3 cp "${PREFIX}/${db}.dump" - |
		"${COMPOSE[@]}" exec -T postgres pg_restore -U postgres --create "${CLEAN[@]}" -d postgres
done

echo "==> rolling the stack back up"
"${COMPOSE[@]}" up -d --wait

echo "restore complete from ${PREFIX} (${#DBS[@]} database(s) processed)"
